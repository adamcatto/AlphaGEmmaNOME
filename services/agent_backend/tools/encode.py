"""ENCODE cCRE lookup via UCSC Genome Browser API (stable, no auth required).

Uses UCSC's getData/track endpoint to fetch the ENCODE combined cCRE track
(encodeCcreCombined) for a genomic region. This is more reliable than calling
the ENCODE portal directly for region-based queries.
"""

from __future__ import annotations

from typing import Any

from ._base import SessionAwareTool, http_client

_UCSC_API = "https://api.genome.ucsc.edu"

_ASSEMBLY_MAP = {"human": "hg38", "mouse": "mm39"}

# Map cCRE class codes to readable labels
_CCRE_CLASSES = {
    "pELS": "proximal enhancer-like signature",
    "dELS": "distal enhancer-like signature",
    "PLS": "promoter-like signature",
    "CTCF-only": "CTCF-only element",
    "DNase-H3K4me3": "DNase-H3K4me3 element",
}


class QueryEncodeElements(SessionAwareTool):
    name = "query_encode_elements"
    description = (
        "Retrieve ENCODE candidate cis-regulatory elements (cCREs) overlapping a genomic "
        "locus. Returns element class (promoter-like, enhancer-like, CTCF-only), chromatin "
        "accessibility signal, and coordinates. Use to ask what regulatory elements are "
        "known in a region."
    )
    inputs = {
        "locus": {"type": "string", "description": "Genomic locus 'chrN:start-end', e.g. 'chr17:43044295-43125483'."},
        "organism": {"type": "string", "description": "'human' or 'mouse'. Default 'human'.", "nullable": True},
        "max_results": {"type": "integer", "description": "Max elements to return (default 50).", "nullable": True},
    }
    output_type = "object"

    def forward(self, locus: str, organism: str = "human", max_results: int = 50) -> dict[str, Any]:
        chrom, coords = _parse_locus(locus)
        if chrom is None:
            return {"error": f"Could not parse locus {locus!r}. Expected 'chrN:start-end'."}
        start, end = coords

        assembly = _ASSEMBLY_MAP.get(organism, "hg38")
        client = http_client()

        r = client.get(
            f"{_UCSC_API}/getData/track",
            params={
                "genome": assembly,
                "track": "encodeCcreCombined",
                "chrom": chrom,
                "start": start,
                "end": end,
            },
            timeout=20,
        )

        if r.status_code == 404:
            # Track may not exist for this assembly; try alt name
            r = client.get(
                f"{_UCSC_API}/getData/track",
                params={"genome": assembly, "track": "encRegTfbsClustered", "chrom": chrom, "start": start, "end": end},
                timeout=20,
            )

        if r.status_code != 200:
            return {"error": f"UCSC API error ({r.status_code}) fetching cCREs for {locus}."}

        data = r.json()
        raw_elements = data.get("encodeCcreCombined") or data.get("encRegTfbsClustered") or []

        if not raw_elements:
            return {
                "locus": locus,
                "organism": organism,
                "elements": [],
                "note": "No ENCODE cCREs found in this region.",
            }

        elements: list[dict] = []
        for el in raw_elements[:max_results]:
            ccre_class_code = el.get("ucscLabel") or el.get("name") or ""
            elements.append({
                "chrom": el.get("chrom"),
                "start": el.get("chromStart"),
                "end": el.get("chromEnd"),
                "name": el.get("name", ""),
                "score": el.get("score"),
                "ccre_class": _CCRE_CLASSES.get(ccre_class_code, ccre_class_code),
                "ccre_code": ccre_class_code,
                "signal": el.get("signalValue") or el.get("score"),
            })

        class_counts: dict[str, int] = {}
        for el in elements:
            c = el["ccre_class"]
            class_counts[c] = class_counts.get(c, 0) + 1

        return {
            "locus": locus,
            "organism": organism,
            "assembly": assembly,
            "total_elements": len(elements),
            "elements": elements,
            "class_summary": class_counts,
            "summary": (
                f"Found {len(elements)} ENCODE cCREs in {locus}. "
                + "; ".join(f"{c}: {n}" for c, n in class_counts.items())
            ),
        }


def _parse_locus(locus: str) -> tuple[str | None, tuple[int, int] | None]:
    try:
        chrom, rest = locus.split(":")
        start_str, end_str = rest.split("-")
        return chrom, (int(start_str), int(end_str))
    except Exception:
        return None, None
