from __future__ import annotations

import json
import logging
from typing import Any

from schema import load_settings

from ._base import SessionAwareTool, http_client
from .macros import _emit

logger = logging.getLogger(__name__)


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
            "description": (
                "Sub-region 'chrN:start-end' to apply edits, or a relative spec like "
                "'10308258±100' for ±100 bp around a coordinate. Prefer design_region_half_bp "
                "when you only need ±N bp around the locus center. Defaults to ±50 bp around center."
            ),
            "nullable": True,
        },
        "design_region_half_bp": {
            "type": "integer",
            "description": (
                "Half-width in bp for the design window when design_region is omitted. "
                "E.g. 100 → ±100 bp around the locus center. Preferred over embedding ±N in design_region."
            ),
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
        "max_candidates": {
            "type": "integer",
            "description": (
                "Maximum number of edit candidates to evaluate. For deletions: places exactly N "
                "10bp deletions evenly spaced across the full input locus sequence. For other edit "
                "types: subsamples evenly from the candidate pool in the design region. "
                "Default: exhaustive search (deletions use sliding window in design_region)."
            ),
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

    def _call_optimize_edits(self, service_url: str, body: dict[str, Any]) -> dict[str, Any]:
        client = http_client()
        stream_url = f"{service_url}/optimize_edits/stream"

        _emit(
            self.session_context,
            "progress",
            {
                "stage": "optimize_edits_start",
                "text": f"Starting {body.get('edit_type', 'edit')} search…",
            },
        )

        with client.stream("POST", stream_url, json=body, timeout=600) as response:
            if response.status_code != 200:
                response.read()
                return {
                    "error": (
                        f"Genome edit optimization failed ({response.status_code}): "
                        f"{response.text[:300]}"
                    )
                }

            result: dict[str, Any] | None = None
            for line in response.iter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if event.get("event") == "progress":
                    current = event.get("current", 0)
                    total = event.get("total", 0)
                    detail = event.get("text", "")
                    _emit(
                        self.session_context,
                        "progress",
                        {
                            "stage": "optimize_edits",
                            "text": f"{current}/{total}: {detail}",
                            "current": current,
                            "total": total,
                        },
                    )
                elif event.get("event") == "result":
                    result = event.get("data")
                elif event.get("event") == "error":
                    return {"error": event.get("message", "optimize_edits failed")}

        if result is None:
            return {"error": "Genome edit optimization returned no result."}
        return result

    def forward(
        self,
        locus: str,
        edit_type: str,
        objective_head: str,
        objective_track: int,
        objective_mode: str,
        design_region: str | None = None,
        design_region_half_bp: int | None = None,
        target_value: float | None = None,
        max_edits: int = 1,
        max_candidates: int | None = None,
        organism: str = "human",
        top_k: int = 5,
        motif_name: str | None = None,
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome

        body: dict[str, Any] = {
            "locus": locus,
            "edit_type": edit_type,
            "objective_head": objective_head,
            "objective_track": objective_track,
            "objective_mode": objective_mode,
            "target_value": target_value,
            "max_edits": max_edits,
            "max_candidates": max_candidates,
            "organism": organism,
            "top_k": top_k,
            "motif_name": motif_name,
        }
        if design_region is not None:
            body["design_region"] = design_region
        if design_region_half_bp is not None:
            body["design_region_half_bp"] = design_region_half_bp

        res_json = self._call_optimize_edits(cfg.service_url, body)
        if "error" in res_json:
            return res_json

        ref_seq = res_json.get("ref_seq")
        edited_seq = res_json.get("edited_seq")
        best = (res_json.get("candidates") or [None])[0]

        if self.session_context is not None and ref_seq and edited_seq:
            from .macros import _predict, _store_prediction, _stash_viz

            if len(edited_seq) != len(ref_seq):
                logger.warning(
                    "Edited sequence length %d != reference length %d; viz may be misaligned.",
                    len(edited_seq),
                    len(ref_seq),
                )

            try:
                ref_pred = _predict(
                    locus=res_json["locus"],
                    sequence=None,
                    heads=[objective_head],
                    resolution="128bp",
                    organism=organism,
                    session_context=self.session_context,
                )
                edited_pred = _predict(
                    locus=None,
                    sequence=edited_seq,
                    heads=[objective_head],
                    resolution="128bp",
                    organism=organism,
                    session_context=self.session_context,
                    use_cache=False,
                )

                _store_prediction(self.session_context, ref_pred, res_json["locus"], "128bp", organism)
                _store_prediction(self.session_context, edited_pred, res_json["locus"], "128bp", organism)

                res_json["viz_spec"] = {
                    "type": "igv_tracks",
                    "prediction_id": ref_pred["prediction_id"],
                    "compare_prediction_id": edited_pred["prediction_id"],
                    "locus": res_json["locus"],
                    "head": objective_head,
                    "track_indices": [objective_track],
                }
                if best:
                    res_json["viz_best_edit"] = {
                        "position": best.get("position"),
                        "sequence_change": best.get("sequence_change"),
                        "predicted_signal": best.get("predicted_signal"),
                    }

                return _stash_viz(self.session_context, res_json)
            except Exception as e:
                logger.warning(f"Comparative visualization prediction failed: {e}", exc_info=True)
                res_json["warning"] = f"Comparative visualization could not be generated: {e}"

        return res_json
