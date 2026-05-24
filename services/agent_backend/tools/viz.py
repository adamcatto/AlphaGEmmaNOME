from __future__ import annotations

from typing import Any

from ._base import SessionAwareTool


class RenderPanel(SessionAwareTool):
    name = "render_panel"
    description = (
        "Tell the frontend to render a visualization panel from the most recent prediction. "
        "Call this after predict_tracks or predict_variant_effect when the user would "
        "benefit from seeing the result. No-op on the backend; emits a viz_spec event."
    )
    inputs = {
        "panel_type": {
            "type": "string",
            "description": "'igv_tracks' | 'contact_map' | 'splice_arcs' | 'variant_delta'",
        },
        "head": {"type": "string", "description": "Which head to visualize (e.g. 'chip_tf').", "nullable": True},
        "track_indices": {
            "type": "array",
            "description": "Optional subset of track indices to display; otherwise top-5 by signal.",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(
        self,
        panel_type: str,
        head: str | None = None,
        track_indices: list[int] | None = None,
    ) -> dict[str, Any]:
        if self.session_context is None or self.session_context.last_prediction is None:
            return {"error": "No prediction to render — call predict_tracks first."}
        spec = {
            "type": panel_type,
            "prediction_id": self.session_context.last_prediction["prediction_id"],
            "locus": self.session_context.last_prediction.get("locus"),
            "head": head,
            "track_indices": track_indices,
        }
        return {"viz_spec": spec}
