from __future__ import annotations

from typing import Any

from schema import load_settings

from ._base import SessionAwareTool, http_client


class ListTracksByAssay(SessionAwareTool):
    name = "list_tracks_by_assay"
    description = (
        "List AlphaGenome tracks that match an assay and/or tissue filter. The `assay` "
        "substring matches the track's assay_title or target_label (e.g. 'CTCF', 'H3K27ac', "
        "'DNase'); `tissue` matches biosample_name, gtex_tissue, or gtex_tissue_group "
        "(e.g. 'brain', 'liver'). Use to find which track indices are relevant."
    )
    inputs = {
        "assay": {
            "type": "string",
            "description": "Substring of assay_title or target_label (e.g. 'CTCF', 'H3K27ac').",
            "nullable": True,
        },
        "tissue": {
            "type": "string",
            "description": "Substring of biosample_name / gtex_tissue (e.g. 'brain', 'liver').",
            "nullable": True,
        },
        "head": {
            "type": "string",
            "description": "Restrict to one head (e.g. 'chip_tf', 'chip_histone').",
            "nullable": True,
        },
        "organism": {
            "type": "string",
            "description": "'human' or 'mouse'.",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(
        self,
        assay: str | None = None,
        tissue: str | None = None,
        head: str | None = None,
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings()
        params: dict[str, Any] = {"organism": organism}
        if assay:
            params["assay"] = assay
        if tissue:
            params["tissue"] = tissue
        if head:
            params["head"] = head
        r = http_client().get(f"{cfg.alphagenome.service_url}/tracks", params=params)
        r.raise_for_status()
        tracks = r.json()["tracks"]
        return {"count": len(tracks), "tracks": tracks[:50]}
