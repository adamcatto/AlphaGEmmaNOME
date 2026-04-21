"""Splicing analysis using AlphaGenome splice heads."""

from __future__ import annotations

from typing import Any

import numpy as np

from schema import load_settings

from ._base import SessionAwareTool
from .macros import (
    _ensembl_lookup,
    _fetch_head_metadata,
    _predict,
    _rank_expression_tracks,
    _stash_viz,
    _store_prediction,
    _window_around,
)

_SPLICE_HEADS = ["splice_sites", "splice_junctions", "splice_site_usage"]


class AnalyzeSplicing(SessionAwareTool):
    name = "analyze_splicing"
    description = (
        "Predict splicing patterns for a gene using AlphaGenome splice heads. "
        "Returns predicted splice sites, junctions, and splice-site usage across cell "
        "types. Use for questions about alternative splicing, exon skipping, or "
        "tissue-specific isoforms of a gene."
    )
    inputs = {
        "gene_symbol": {
            "type": "string",
            "description": "HGNC gene symbol, e.g. 'NRXN1', 'TP53', 'BRCA1'.",
        },
        "tissue_keywords": {
            "type": "array",
            "description": "Optional tissue substrings to filter tracks (e.g. ['brain', 'neuron']).",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Top tracks per splice head. Default 5.",
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
        gene_symbol: str,
        tissue_keywords: list[str] | None = None,
        top_k: int = 5,
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome
        try:
            gene = _ensembl_lookup(gene_symbol, organism)
        except LookupError as e:
            return {"error": str(e)}

        gene_center = (gene["start"] + gene["end"]) // 2
        locus = _window_around(gene["chrom"], gene_center, cfg.max_input_length)

        # Use 1bp resolution so splice site positions are interpretable
        pred = _predict(locus, None, _SPLICE_HEADS, "1bp", organism, self.session_context)
        decoded = _store_prediction(self.session_context, pred, locus, "1bp", organism)

        by_head: dict[str, Any] = {}
        notes: list[str] = []
        for head in _SPLICE_HEADS:
            if head not in decoded:
                continue
            arr = decoded[head]
            metadata = _fetch_head_metadata(head, organism)
            high, _low, stats, note = _rank_expression_tracks(
                arr, metadata, tissue_keywords, top_k, 0
            )
            by_head[head] = {"top_tracks": high, "signal_stats": stats}
            if note:
                notes.append(f"[{head}] {note}")

        primary_head = next(
            (h for h in _SPLICE_HEADS if h in by_head), next(iter(by_head), None)
        )
        viz_indices = [t["track_index"] for t in by_head.get(primary_head or "", {}).get("top_tracks", [])]

        return _stash_viz(
            self.session_context,
            {
                "gene": gene,
                "window": locus,
                "heads": _SPLICE_HEADS,
                "splicing_by_head": by_head,
                "notes": notes or None,
                "summary": (
                    f"Predicted splicing patterns for {gene_symbol} across "
                    f"{len(by_head)} splice heads."
                ),
                "viz_spec": {
                    "type": "igv_tracks",
                    "prediction_id": pred["prediction_id"],
                    "locus": locus,
                    "head": primary_head or "splice_sites",
                    "track_indices": viz_indices,
                } if primary_head else None,
            },
        )
