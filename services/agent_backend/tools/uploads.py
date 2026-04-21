from __future__ import annotations

from typing import Any

from ._base import SessionAwareTool


class UploadSequence(SessionAwareTool):
    name = "upload_sequence"
    description = (
        "Reference a DNA sequence previously uploaded by the user via the upload endpoint. "
        "Returns the sequence bound to this session under `upload_id`."
    )
    inputs = {
        "upload_id": {"type": "string", "description": "Upload identifier returned by /upload."}
    }
    output_type = "object"

    def forward(self, upload_id: str) -> dict[str, Any]:
        if self.session_context is None:
            return {"error": "No session context available."}
        upload = self.session_context.uploads.get(upload_id)
        if upload is None:
            return {"error": f"Upload {upload_id!r} not found in this session."}
        return {
            "upload_id": upload_id,
            "length": len(upload["sequence"]),
            "source": upload.get("source"),
            "sequence_preview": upload["sequence"][:60] + ("…" if len(upload["sequence"]) > 60 else ""),
        }
