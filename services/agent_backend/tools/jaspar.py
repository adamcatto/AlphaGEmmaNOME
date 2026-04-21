"""JASPAR TF motif scanning via JASPAR REST API (no auth required)."""

from __future__ import annotations

from typing import Any

from ._base import SessionAwareTool, http_client

_JASPAR_API = "https://jaspar.genereg.net/api/v1"
_DEFAULT_THRESHOLD = 0.80


class ScanJasparMotifs(SessionAwareTool):
    name = "scan_jaspar_motifs"
    description = (
        "Scan a DNA sequence for transcription factor binding site motifs using the "
        "JASPAR database. Returns TF name, position, strand, and match score. Use "
        "when the user wants to know which TFs can bind to a specific DNA sequence or "
        "to analyze a regulatory region for motif content."
    )
    inputs = {
        "sequence": {"type": "string", "description": "DNA sequence to scan (A/C/G/T/N). Recommended length: 100–2000 bp."},
        "tf_names": {"type": "array", "items": {"type": "string"}, "description": "Optional list of specific TF names to restrict to (e.g. ['CTCF', 'SP1']). If omitted, scans vertebrate CORE matrices.", "nullable": True},
        "threshold": {"type": "number", "description": "Relative score threshold 0–1 (default 0.80). Lower = more permissive.", "nullable": True},
        "tax_group": {"type": "string", "description": "Taxonomic group to scan: 'vertebrates' (default), 'insects', 'plants', 'fungi', 'nematodes', 'urochordates'.", "nullable": True},
    }
    output_type = "object"

    def forward(
        self,
        sequence: str,
        tf_names: list[str] | None = None,
        threshold: float = _DEFAULT_THRESHOLD,
        tax_group: str = "vertebrates",
    ) -> dict[str, Any]:
        sequence = sequence.upper().replace(" ", "").replace("\n", "")
        if not sequence or any(c not in "ACGTN" for c in sequence):
            return {"error": "Invalid DNA sequence. Only A/C/G/T/N characters allowed."}
        if len(sequence) > 10_000:
            return {"error": "Sequence too long for motif scan (max 10,000 bp). Use a shorter window."}

        client = http_client()
        matrix_ids = _resolve_matrix_ids(client, tf_names, tax_group)
        if isinstance(matrix_ids, dict):
            return matrix_ids  # error dict

        scan_r = client.post(
            f"{_JASPAR_API}/tools/scanner/",
            json={
                "sequences": [{"id": "input", "sequence": sequence}],
                "matrix_ids": matrix_ids,
                "threshold": threshold,
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=60,
        )

        if scan_r.status_code != 200:
            return {"error": f"JASPAR scanner API error ({scan_r.status_code}): {scan_r.text[:300]}"}

        raw_hits = scan_r.json()
        if isinstance(raw_hits, list):
            hits_list = raw_hits
        elif isinstance(raw_hits, dict):
            hits_list = raw_hits.get("results", raw_hits.get("hits", []))
        else:
            hits_list = []

        hits: list[dict] = []
        for h in hits_list:
            hits.append({
                "tf_name": h.get("name") or h.get("matrix_id", ""),
                "matrix_id": h.get("matrix_id", ""),
                "position": h.get("start"),
                "end": h.get("end"),
                "strand": h.get("strand", "+"),
                "score": round(float(h.get("score", 0)), 4),
                "relative_score": round(float(h.get("rel_score", h.get("score", 0))), 4),
                "matched_seq": h.get("sequence", ""),
            })

        hits.sort(key=lambda x: -x["relative_score"])

        tf_counts: dict[str, int] = {}
        for h in hits:
            tf_counts[h["tf_name"]] = tf_counts.get(h["tf_name"], 0) + 1
        top_tfs = sorted(tf_counts.items(), key=lambda x: -x[1])[:10]

        return {
            "sequence_length": len(sequence),
            "total_hits": len(hits),
            "threshold": threshold,
            "top_tfs": [{"tf": t, "hit_count": c} for t, c in top_tfs],
            "hits": hits[:100],  # cap at 100 for response size
            "summary": (
                f"Found {len(hits)} motif instances above threshold {threshold} in {len(sequence)}-bp sequence. "
                f"Most frequent: {', '.join(t for t, _ in top_tfs[:3])}."
                if hits else f"No motif instances found above threshold {threshold}."
            ),
        }


def _resolve_matrix_ids(client, tf_names: list[str] | None, tax_group: str) -> list[str] | dict:
    """Return a list of JASPAR matrix IDs to scan.

    If tf_names is given, look up each TF by name. Otherwise return IDs for
    the CORE collection of the given taxonomic group (up to 200 matrices).
    """
    if tf_names:
        ids: list[str] = []
        for name in tf_names:
            try:
                r = client.get(
                    f"{_JASPAR_API}/matrix/",
                    params={"name": name, "collection": "CORE", "format": "json", "page_size": 5},
                    timeout=10,
                )
                if r.status_code == 200:
                    for m in r.json().get("results", []):
                        ids.append(m["matrix_id"])
            except Exception:
                pass
        if not ids:
            return {"error": f"None of the requested TF names {tf_names} found in JASPAR CORE."}
        return ids

    try:
        r = client.get(
            f"{_JASPAR_API}/matrix/",
            params={"collection": "CORE", "tax_group": tax_group, "format": "json", "page_size": 200},
            timeout=15,
        )
        if r.status_code != 200:
            return {"error": f"JASPAR matrix list error ({r.status_code})."}
        return [m["matrix_id"] for m in r.json().get("results", [])]
    except Exception as e:
        return {"error": f"JASPAR matrix lookup failed: {e}"}
