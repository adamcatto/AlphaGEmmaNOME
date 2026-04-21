"""Genomic conservation scores via UCSC Genome Browser API (no auth required).

Returns phyloP (per-base evolutionary conservation) and phastCons (probability
of conserved element) scores for a locus.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ._base import SessionAwareTool, http_client

_UCSC_API = "https://api.genome.ucsc.edu"
_ASSEMBLY_MAP = {"human": "hg38", "mouse": "mm39"}

# Preferred track names in priority order
_PHYLOP_TRACKS = ["phyloP100way", "phyloP30way", "phyloP17way"]
_PHASTCONS_TRACKS = ["phastCons100way", "phastCons30way", "phastCons17way"]


class QueryConservation(SessionAwareTool):
    name = "query_conservation"
    description = (
        "Fetch per-position phyloP and phastCons conservation scores for a genomic locus "
        "from the UCSC Genome Browser. High phyloP scores indicate strong evolutionary "
        "constraint; phastCons reflects probability of conserved element membership. "
        "Use to assess evolutionary conservation of a region or variant site."
    )
    inputs = {
        "locus": {"type": "string", "description": "Genomic locus 'chrN:start-end', e.g. 'chr17:43044295-43125483'."},
        "organism": {"type": "string", "description": "'human' or 'mouse'. Default 'human'.", "nullable": True},
        "include_per_base": {"type": "boolean", "description": "Whether to include per-base score arrays (can be large). Default false.", "nullable": True},
    }
    output_type = "object"

    def forward(
        self,
        locus: str,
        organism: str = "human",
        include_per_base: bool = False,
    ) -> dict[str, Any]:
        chrom, coords = _parse_locus(locus)
        if chrom is None:
            return {"error": f"Could not parse locus {locus!r}. Expected 'chrN:start-end'."}
        start, end = coords

        assembly = _ASSEMBLY_MAP.get(organism, "hg38")
        client = http_client()

        phylop_result = _fetch_track(client, assembly, chrom, start, end, _PHYLOP_TRACKS)
        phastcons_result = _fetch_track(client, assembly, chrom, start, end, _PHASTCONS_TRACKS)

        response: dict[str, Any] = {
            "locus": locus,
            "organism": organism,
            "assembly": assembly,
        }

        if phylop_result:
            scores = np.array(phylop_result["scores"], dtype=float)
            response["phyloP"] = {
                "track": phylop_result["track"],
                "mean": float(np.mean(scores)),
                "max": float(np.max(scores)),
                "min": float(np.min(scores)),
                "fraction_positive": float(np.mean(scores > 0)),
                "fraction_highly_conserved": float(np.mean(scores > 2.0)),
            }
            if include_per_base:
                response["phyloP"]["scores"] = [round(float(s), 3) for s in scores[:2000]]

        if phastcons_result:
            scores = np.array(phastcons_result["scores"], dtype=float)
            response["phastCons"] = {
                "track": phastcons_result["track"],
                "mean": float(np.mean(scores)),
                "max": float(np.max(scores)),
                "fraction_conserved_element": float(np.mean(scores > 0.5)),
            }
            if include_per_base:
                response["phastCons"]["scores"] = [round(float(s), 3) for s in scores[:2000]]

        if response.get("phyloP") or response.get("phastCons"):
            response["summary"] = _summarize(locus, response.get("phyloP"), response.get("phastCons"))
        else:
            response["note"] = "Conservation data not available for this region/assembly."

        return response


def _fetch_track(
    client,
    assembly: str,
    chrom: str,
    start: int,
    end: int,
    track_priority: list[str],
) -> dict[str, Any] | None:
    for track in track_priority:
        try:
            r = client.get(
                f"{_UCSC_API}/getData/track",
                params={"genome": assembly, "track": track, "chrom": chrom, "start": start, "end": end},
                timeout=20,
            )
            if r.status_code == 200:
                data = r.json()
                scores = data.get(track)
                if scores is not None:
                    if isinstance(scores, list):
                        return {"track": track, "scores": scores}
                    # Some UCSC tracks return {"data": [...]}
                    if isinstance(scores, dict) and "data" in scores:
                        return {"track": track, "scores": scores["data"]}
        except Exception:
            continue
    return None


def _parse_locus(locus: str) -> tuple[str | None, tuple[int, int] | None]:
    try:
        chrom, rest = locus.split(":")
        start_str, end_str = rest.split("-")
        return chrom, (int(start_str), int(end_str))
    except Exception:
        return None, None


def _summarize(locus: str, phylop: dict | None, phastcons: dict | None) -> str:
    parts = [f"Conservation summary for {locus}:"]
    if phylop:
        mean = phylop["mean"]
        frac_cons = phylop.get("fraction_highly_conserved", 0)
        if mean > 1.5:
            parts.append(f"Region is highly conserved (mean phyloP={mean:.2f}; {frac_cons*100:.0f}% of bases with phyloP>2).")
        elif mean > 0:
            parts.append(f"Moderate conservation (mean phyloP={mean:.2f}).")
        else:
            parts.append(f"Region shows little evolutionary constraint (mean phyloP={mean:.2f}).")
    if phastcons:
        frac = phastcons.get("fraction_conserved_element", 0)
        if frac > 0.5:
            parts.append(f"{frac*100:.0f}% of bases fall in conserved elements (phastCons>0.5).")
    return " ".join(parts)
