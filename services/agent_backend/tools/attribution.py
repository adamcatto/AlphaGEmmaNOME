"""Sequence attribution via in-silico mutagenesis (ISM) at the alphagenome_svc."""

from __future__ import annotations

from typing import Any

from schema import load_settings

from ._base import SessionAwareTool, http_client


class AnalyzeAttribution(SessionAwareTool):
    name = "analyze_attribution"
    description = (
        "Compute per-nucleotide importance scores for a genomic locus using "
        "in-silico mutagenesis (ISM). Each position in the attribution region is "
        "individually mutated to all three non-reference bases; the maximum absolute "
        "delta in the target track's signal gives the importance score. Returns "
        "per-position importance scores and per-base deltas. "
        "WARNING: requires N×3 AlphaGenome forward passes — keep attribution_region "
        "≤64 bp to stay under ~3 minutes runtime."
    )
    inputs = {
        "locus": {
            "type": "string",
            "description": "Full AlphaGenome input window 'chrN:start-end' (e.g. 131 kb).",
        },
        "head": {
            "type": "string",
            "description": "AlphaGenome head to attribute, e.g. 'atac', 'chip_tf', 'rna_seq'.",
        },
        "track_index": {
            "type": "integer",
            "description": "Track index within the head.",
        },
        "attribution_region": {
            "type": "string",
            "description": (
                "Sub-region to run ISM over 'chrN:start-end' (≤64 bp recommended). "
                "Defaults to ±32 bp around the locus center."
            ),
            "nullable": True,
        },
        "resolution": {
            "type": "string",
            "description": "'1bp' or '128bp'. Default '128bp'.",
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
        locus: str,
        head: str,
        track_index: int,
        attribution_region: str | None = None,
        resolution: str = "128bp",
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome
        client = http_client()

        body: dict[str, Any] = {
            "locus": locus,
            "head": head,
            "track_index": track_index,
            "resolution": resolution,
            "organism": organism,
        }
        if attribution_region is not None:
            body["attribution_region"] = attribution_region

        r = client.post(f"{cfg.service_url}/attribution", json=body, timeout=600)
        if r.status_code != 200:
            return {"error": f"Attribution failed ({r.status_code}): {r.text[:300]}"}

        return r.json()
