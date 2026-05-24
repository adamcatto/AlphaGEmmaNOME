from __future__ import annotations

from typing import Any

from schema import load_settings

from ._base import SessionAwareTool, http_client


class OptimizeEdits(SessionAwareTool):
    name = "optimize_edits"
    description = (
        "Find optimal genome edits (multi-site SNVs, deletions, insertions, or motif modifications) "
        "to achieve a target functional effect on a specified AlphaGenome track. Supports maximizing, "
        "minimizing, or targeting specific track values."
    )
    inputs = {
        "locus": {
            "type": "string",
            "description": "Full genomic locus 'chrN:start-end' containing the editing window.",
        },
        "edit_type": {
            "type": "string",
            "description": "Type of edit to search: 'snv' (substitutions), 'deletion' (sliding window), 'insertion' (insert consensus motif), or 'motif' (ablate or insert matched motifs).",
        },
        "objective_head": {
            "type": "string",
            "description": "Target AlphaGenome head, e.g. 'atac', 'cage', 'rna_seq', 'chip_tf'.",
        },
        "objective_track": {
            "type": "integer",
            "description": "Track index within the head.",
        },
        "objective_mode": {
            "type": "string",
            "description": "Optimization goal: 'maximize' (increase signal), 'minimize' (silence/decrease), or 'target' (match a specific value).",
        },
        "design_region": {
            "type": "string",
            "description": "Sub-region 'chrN:start-end' (≤100 bp recommended) to apply edits. Defaults to ±50 bp around the locus center.",
            "nullable": True,
        },
        "target_value": {
            "type": "number",
            "description": "Target numeric value for the signal (required when objective_mode is 'target').",
            "nullable": True,
        },
        "max_edits": {
            "type": "integer",
            "description": "Maximum number of simultaneous edits (e.g., up to 3 for SNVs). Default 1.",
            "nullable": True,
        },
        "organism": {
            "type": "string",
            "description": "'human' or 'mouse'. Default 'human'.",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Number of top candidates to return. Default 5.",
            "nullable": True,
        },
        "motif_name": {
            "type": "string",
            "description": "Transcription factor motif name to insert or ablate (e.g. 'CTCF', 'SP1', 'AP-1', 'TATA', 'OCT4', 'NF-kB').",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(
        self,
        locus: str,
        edit_type: str,
        objective_head: str,
        objective_track: int,
        objective_mode: str,
        design_region: str | None = None,
        target_value: float | None = None,
        max_edits: int = 1,
        organism: str = "human",
        top_k: int = 5,
        motif_name: str | None = None,
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome
        client = http_client()

        body: dict[str, Any] = {
            "locus": locus,
            "edit_type": edit_type,
            "objective_head": objective_head,
            "objective_track": objective_track,
            "objective_mode": objective_mode,
            "target_value": target_value,
            "max_edits": max_edits,
            "organism": organism,
            "top_k": top_k,
            "motif_name": motif_name,
        }
        if design_region is not None:
            body["design_region"] = design_region

        # 600-second timeout to handle multi-site beam searches or large runs
        r = client.post(f"{cfg.service_url}/optimize_edits", json=body, timeout=600)
        if r.status_code != 200:
            return {"error": f"Genome edit optimization failed ({r.status_code}): {r.text[:300]}"}

        return r.json()
