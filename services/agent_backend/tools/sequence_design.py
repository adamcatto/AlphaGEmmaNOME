"""Sequence optimization via in-silico mutagenesis at the alphagenome_svc."""

from __future__ import annotations

from typing import Any

from schema import load_settings

from ._base import SessionAwareTool, http_client


class OptimizeSequence(SessionAwareTool):
    name = "optimize_sequence"
    description = (
        "Find single-nucleotide mutations that maximize signal for a target "
        "AlphaGenome track. Scans every position in the design_region with all "
        "three alternate bases and returns the top-k mutations ranked by signal "
        "increase. Use for questions like 'what mutation would increase H3K27ac "
        "signal at this enhancer?' or 'how can I improve CAGE signal at this TSS?'."
    )
    inputs = {
        "locus": {
            "type": "string",
            "description": "Full AlphaGenome input window 'chrN:start-end'.",
        },
        "head": {
            "type": "string",
            "description": "Target AlphaGenome head to maximize, e.g. 'atac', 'cage', 'rna_seq'.",
        },
        "track_index": {
            "type": "integer",
            "description": "Track index within the head.",
        },
        "design_region": {
            "type": "string",
            "description": (
                "Sub-region to scan for mutations 'chrN:start-end' (≤100 bp recommended). "
                "Defaults to ±50 bp around the locus center."
            ),
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Number of top candidate mutations to return. Default 5.",
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
        design_region: str | None = None,
        top_k: int = 5,
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome
        client = http_client()

        body: dict[str, Any] = {
            "locus": locus,
            "head": head,
            "track_index": track_index,
            "top_k": top_k,
            "organism": organism,
        }
        if design_region is not None:
            body["design_region"] = design_region

        r = client.post(f"{cfg.service_url}/optimize_sequence", json=body, timeout=600)
        if r.status_code != 200:
            return {"error": f"Sequence optimization failed ({r.status_code}): {r.text[:300]}"}

        return r.json()
