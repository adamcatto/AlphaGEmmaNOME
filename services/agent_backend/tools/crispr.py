"""CRISPR-Cas9 guide RNA design with AlphaGenome on-target scoring."""

from __future__ import annotations

import base64
from typing import Any

import numpy as np

from schema import load_settings

from ._base import SessionAwareTool, http_client
from .macros import _ensembl_lookup, _predict, _svc_url, _window_around

_GUIDE_LEN = 20
_VALID_BASES = frozenset("ACGT")


def _decode_array(arr_obj: dict) -> np.ndarray:
    raw = base64.b64decode(arr_obj["data_b64"])
    return np.frombuffer(raw, dtype=arr_obj["dtype"]).reshape(arr_obj["shape"])


def _rc(seq: str) -> str:
    """Reverse complement a DNA sequence."""
    comp = str.maketrans("ACGTacgt", "TGCAtgca")
    return seq[::-1].translate(comp)


def _scan_pam(sequence: str, offset: int) -> list[dict]:
    """Scan sequence for NGG PAM sites on both strands.

    Returns guide candidates with their genomic cut position.
    offset: genomic coordinate of sequence[0].
    """
    seq = sequence.upper()
    n = len(seq)
    candidates: list[dict] = []

    # Forward strand: guide is [i, i+20), PAM is seq[i+20:i+23] = N-G-G
    for i in range(n - _GUIDE_LEN - 2):
        pam2 = seq[i + _GUIDE_LEN + 1]
        pam3 = seq[i + _GUIDE_LEN + 2]
        if pam2 == "G" and pam3 == "G":
            guide = seq[i : i + _GUIDE_LEN]
            if all(c in _VALID_BASES for c in guide):
                cut_pos = offset + i + _GUIDE_LEN - 3
                candidates.append(
                    {"guide": guide, "strand": "+", "cut_position": cut_pos}
                )

    # Reverse strand: reverse complement the window, same logic
    rc_seq = _rc(seq)
    for i in range(len(rc_seq) - _GUIDE_LEN - 2):
        pam2 = rc_seq[i + _GUIDE_LEN + 1]
        pam3 = rc_seq[i + _GUIDE_LEN + 2]
        if pam2 == "G" and pam3 == "G":
            guide = rc_seq[i : i + _GUIDE_LEN]
            if all(c in _VALID_BASES for c in guide):
                # Map back to forward-strand genomic coordinate
                cut_pos = offset + (n - (i + _GUIDE_LEN - 3)) - 1
                candidates.append(
                    {"guide": guide, "strand": "-", "cut_position": cut_pos}
                )

    return candidates


def _gc(seq: str) -> float:
    return sum(c in "GC" for c in seq.upper()) / max(len(seq), 1)


class DesignCrisprGuide(SessionAwareTool):
    name = "design_crispr_guide"
    description = (
        "Design and score SpCas9 (NGG PAM) guide RNAs targeting a gene. Scans a window "
        "around the gene's TSS or coding region for PAM sites, then scores candidates by "
        "GC content, proximity to target, and AlphaGenome-predicted chromatin accessibility "
        "at the cut site. Returns ranked guide RNA sequences with position and scores. "
        "Use for 'design a CRISPR guide to knock out gene X' questions."
    )
    inputs = {
        "gene_symbol": {
            "type": "string",
            "description": "HGNC gene symbol, e.g. 'BRCA1', 'TP53'.",
        },
        "target_region": {
            "type": "string",
            "description": (
                "'tss' (default — targets promoter/TSS), 'coding' (targets gene body center), "
                "or a specific locus 'chrN:start-end'."
            ),
            "nullable": True,
        },
        "window_bp": {
            "type": "integer",
            "description": "Sequence window around the target to scan for PAM sites. Default 500 bp.",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Number of top guide candidates to return. Default 5.",
            "nullable": True,
        },
        "organism": {
            "type": "string",
            "description": "'human' or 'mouse'. Default 'human'.",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(
        self,
        gene_symbol: str,
        target_region: str | None = None,
        window_bp: int = 500,
        top_k: int = 5,
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome
        target_region = target_region or "tss"

        try:
            gene = _ensembl_lookup(gene_symbol, organism)
        except LookupError as e:
            return {"error": str(e)}

        # Resolve target center
        if target_region == "tss":
            center = gene["tss"]
        elif target_region == "coding":
            center = (gene["start"] + gene["end"]) // 2
        elif ":" in str(target_region):
            try:
                parts = str(target_region).split(":")
                coords = parts[1].split("-")
                center = (int(coords[0]) + int(coords[1])) // 2
            except Exception:
                center = gene["tss"]
        else:
            center = gene["tss"]

        half = window_bp // 2
        scan_start = max(1, center - half)
        scan_end = center + half
        scan_locus = f"{gene['chrom']}:{scan_start}-{scan_end}"

        # Fetch sequence for the scan window
        r = http_client().get(f"{_svc_url()}/sequence", params={"locus": scan_locus})
        if r.status_code != 200:
            return {"error": f"Could not fetch sequence for {scan_locus}: {r.text[:200]}"}
        sequence = r.json()["sequence"]

        candidates = _scan_pam(sequence, scan_start)
        if not candidates:
            return {
                "gene": gene,
                "target_region": target_region,
                "scan_locus": scan_locus,
                "total_pam_sites": 0,
                "top_guides": [],
                "summary": f"No NGG PAM sites found in {scan_locus}.",
            }

        # Run AlphaGenome on the full window centered at target for chromatin scoring
        ag_locus = _window_around(gene["chrom"], center, cfg.max_input_length)
        try:
            pred = _predict(
                ag_locus, None, ["atac", "chip_tf"], cfg.default_resolution, organism, self.session_context
            )
            atac_arr = next(
                (_decode_array(a) for a in pred["arrays"] if a["head"] == "atac"), None
            )
        except Exception:
            pred = None
            atac_arr = None

        ag_start = int(ag_locus.split(":")[1].split("-")[0])
        bins_per_bp = 1.0 / 128.0

        scored: list[dict] = []
        for c in candidates:
            gc = _gc(c["guide"])
            # Prefer 40–70 % GC
            gc_score = 1.0 - abs(gc - 0.55) / 0.55

            # Distance-to-target score (exponential decay, half-score at 50 bp)
            dist = abs(c["cut_position"] - center)
            dist_score = 1.0 / (1.0 + dist / 50.0)

            # Chromatin accessibility at the cut site
            chromatin_score = 0.5
            if atac_arr is not None:
                bin_idx = max(0, min(atac_arr.shape[0] - 1, int((c["cut_position"] - ag_start) * bins_per_bp)))
                raw_cs = float(atac_arr[bin_idx].mean())
                # Normalize to [0, 1] with soft cap at 1.0
                chromatin_score = min(raw_cs / (raw_cs + 1.0), 1.0)

            composite = 0.40 * gc_score + 0.30 * dist_score + 0.30 * chromatin_score

            scored.append(
                {
                    **c,
                    "gc_content": round(gc, 2),
                    "gc_score": round(gc_score, 3),
                    "distance_to_target": dist,
                    "distance_score": round(dist_score, 3),
                    "chromatin_score": round(chromatin_score, 3),
                    "composite_score": round(composite, 3),
                }
            )

        scored.sort(key=lambda g: -g["composite_score"])
        top_guides = scored[:top_k]
        best = top_guides[0] if top_guides else None

        return {
            "gene": gene,
            "target_region": target_region,
            "target_center": center,
            "scan_locus": scan_locus,
            "total_pam_sites": len(candidates),
            "top_guides": top_guides,
            "summary": (
                f"Found {len(candidates)} NGG PAM sites in {scan_locus}. "
                f"Top guide: {best['guide']} "
                f"(GC={best['gc_content']:.0%}, score={best['composite_score']:.2f}, "
                f"cut@{best['cut_position']}, strand={best['strand']})."
                if best
                else f"No usable guides found in {scan_locus}."
            ),
        }
