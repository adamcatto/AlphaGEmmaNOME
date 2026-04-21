"""3D chromatin contact analysis using AlphaGenome contact_maps head."""

from __future__ import annotations

import base64
from typing import Any

import numpy as np

from schema import load_settings

from ._base import SessionAwareTool
from .macros import (
    _ensembl_lookup,
    _predict,
    _stash_viz,
    _window_around,
)


def _decode_array(arr_obj: dict) -> np.ndarray:
    raw = base64.b64decode(arr_obj["data_b64"])
    return np.frombuffer(raw, dtype=arr_obj["dtype"]).reshape(arr_obj["shape"])


class Analyze3dContacts(SessionAwareTool):
    name = "analyze_3d_contacts"
    description = (
        "Predict 3D chromatin contacts for a gene or genomic region using "
        "AlphaGenome's contact map head. Returns the top predicted contact pairs "
        "and a contact-map visualization. Use for questions about chromatin "
        "architecture, TADs, enhancer–promoter loops, or 3D genome structure."
    )
    inputs = {
        "gene_symbol": {
            "type": "string",
            "description": "HGNC gene symbol. Mutually exclusive with locus.",
            "nullable": True,
        },
        "locus": {
            "type": "string",
            "description": "Genomic locus 'chrN:start-end'. Mutually exclusive with gene_symbol.",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Top contact pairs to return. Default 10.",
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
        gene_symbol: str | None = None,
        locus: str | None = None,
        top_k: int = 10,
        organism: str = "human",
    ) -> dict[str, Any]:
        if gene_symbol is None and locus is None:
            return {"error": "Provide either gene_symbol or locus."}

        cfg = load_settings().alphagenome
        gene: dict | None = None

        if gene_symbol:
            try:
                gene = _ensembl_lookup(gene_symbol, organism)
            except LookupError as e:
                return {"error": str(e)}
            gene_center = (gene["start"] + gene["end"]) // 2
            locus = _window_around(gene["chrom"], gene_center, cfg.max_input_length)

        pred = _predict(locus, None, ["contact_maps"], "128bp", organism, self.session_context)

        contact_arr_obj = next(
            (a for a in pred["arrays"] if a["head"] == "contact_maps"), None
        )
        if contact_arr_obj is None:
            return {"error": "contact_maps head not available in this model."}

        contact_arr = _decode_array(contact_arr_obj)

        # Shape may be [1, L, L, T] or [L, L, T] or [L, L]
        if contact_arr.ndim == 4:
            contact_arr = contact_arr[0]  # [L, L, T]
        if contact_arr.ndim == 3:
            cm = contact_arr.mean(axis=-1).astype(np.float32)  # [L, L]
        else:
            cm = contact_arr.astype(np.float32)  # [L, L]

        # Guard against unreasonably large arrays
        if cm.nbytes > 50_000_000:
            return {"error": "Contact map too large (>50 MB). Try a smaller locus."}

        cm = (cm + cm.T) / 2  # symmetrize

        n = cm.shape[0]
        cm_upper = np.triu(cm, k=1)  # avoid diagonal and lower triangle duplicates

        flat_order = np.argsort(-cm_upper.ravel())
        pairs: list[dict] = []
        seen: set[tuple] = set()
        for flat_i in flat_order:
            i, j = divmod(int(flat_i), n)
            if i >= j:
                continue
            if (i, j) not in seen:
                seen.add((i, j))
                pairs.append(
                    {
                        "bin_i": i,
                        "bin_j": j,
                        "distance_bins": j - i,
                        "contact_score": round(float(cm[i, j]), 4),
                    }
                )
            if len(pairs) >= top_k:
                break

        top = pairs[0] if pairs else None
        summary = (
            f"Predicted {n}×{n} contact map for {locus}. "
            f"Strongest contact: bins {top['bin_i']}–{top['bin_j']} "
            f"({top['distance_bins']} bins apart, score {top['contact_score']:.3f})."
            if top
            else f"No contacts resolved in {locus}."
        )

        return _stash_viz(
            self.session_context,
            {
                "locus": locus,
                "gene": gene,
                "contact_map_bins": n,
                "top_contacts": pairs,
                "summary": summary,
                "viz_spec": {
                    "type": "contact_map",
                    "prediction_id": pred["prediction_id"],
                    "locus": locus,
                    "head": "contact_maps",
                    "track_indices": [0],
                },
            },
        )
