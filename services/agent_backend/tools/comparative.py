"""Differential regulation analysis comparing two tissue groups."""

from __future__ import annotations

import base64
from typing import Any

import numpy as np

from schema import load_settings

from ._base import SessionAwareTool
from .macros import (
    _TISSUE_FIELDS,
    _ensembl_lookup,
    _fetch_head_metadata,
    _predict,
    _stash_viz,
    _store_prediction,
    _window_around,
)


def _decode_array(arr_obj: dict) -> np.ndarray:
    raw = base64.b64decode(arr_obj["data_b64"])
    return np.frombuffer(raw, dtype=arr_obj["dtype"]).reshape(arr_obj["shape"])


class AnalyzeDifferentialRegulation(SessionAwareTool):
    name = "analyze_differential_regulation"
    description = (
        "Compare AlphaGenome chromatin predictions between two tissue groups for a gene. "
        "Returns the tracks that differ most between tissue_a and tissue_b, plus fold "
        "change and per-tissue mean signals. Use for questions like 'how does BRCA1 "
        "regulation differ between breast and liver?' or 'which TF binding sites are "
        "tissue-specific for this gene?'."
    )
    inputs = {
        "gene_symbol": {
            "type": "string",
            "description": "HGNC gene symbol, e.g. 'BRCA1', 'TP53', 'MYC'.",
        },
        "tissue_a": {
            "type": "string",
            "description": "First tissue group substring, e.g. 'breast'.",
        },
        "tissue_b": {
            "type": "string",
            "description": "Second tissue group substring, e.g. 'liver'.",
        },
        "heads": {
            "type": "array",
            "description": "Heads to compare. Default: ['atac', 'chip_tf', 'rna_seq'].",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Top differential tracks per head per tissue. Default 5.",
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
        tissue_a: str,
        tissue_b: str,
        heads: list[str] | None = None,
        top_k: int = 5,
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome
        heads = heads or ["atac", "chip_tf", "rna_seq"]

        try:
            gene = _ensembl_lookup(gene_symbol, organism)
        except LookupError as e:
            return {"error": str(e)}

        gene_center = (gene["start"] + gene["end"]) // 2
        locus = _window_around(gene["chrom"], gene_center, cfg.max_input_length)

        pred = _predict(locus, None, heads, cfg.default_resolution, organism, self.session_context)
        decoded = _store_prediction(self.session_context, pred, locus, cfg.default_resolution, organism)

        kw_a = tissue_a.lower()
        kw_b = tissue_b.lower()

        differential_by_head: dict[str, Any] = {}
        for head in heads:
            if head not in decoded:
                continue
            arr = decoded[head]  # [seq_len, n_tracks]
            axes = tuple(range(arr.ndim - 1))
            means = arr.mean(axis=axes)  # [n_tracks]

            metadata = _fetch_head_metadata(head, organism)
            idx_to_meta = {m["track_index"]: m for m in metadata}

            indices_a: list[int] = []
            indices_b: list[int] = []
            for i in range(len(means)):
                meta = idx_to_meta.get(i)
                if meta is None:
                    continue
                haystack = " ".join(
                    str(meta.get(f, "") or "") for f in _TISSUE_FIELDS
                ).lower()
                if kw_a in haystack:
                    indices_a.append(i)
                elif kw_b in haystack:
                    indices_b.append(i)

            if not indices_a or not indices_b:
                differential_by_head[head] = {
                    "note": (
                        f"Insufficient tracks: {tissue_a!r} (n={len(indices_a)}), "
                        f"{tissue_b!r} (n={len(indices_b)}) in head '{head}'."
                    )
                }
                continue

            mean_a = float(means[indices_a].mean())
            mean_b = float(means[indices_b].mean())

            def _top_tracks(indices: list[int]) -> list[dict]:
                ranked = sorted(indices, key=lambda i: -float(means[i]))[:top_k]
                result = []
                for i in ranked:
                    entry: dict[str, Any] = {
                        "track_index": i,
                        "mean_signal": round(float(means[i]), 4),
                    }
                    meta = idx_to_meta.get(i)
                    if meta:
                        for k in ("biosample_name", "target_label", "assay_title"):
                            if meta.get(k):
                                entry[k] = meta[k]
                    result.append(entry)
                return result

            differential_by_head[head] = {
                f"{tissue_a}_mean_signal": round(mean_a, 4),
                f"{tissue_b}_mean_signal": round(mean_b, 4),
                "fold_change_a_over_b": round(mean_a / max(mean_b, 1e-9), 3),
                f"top_{tissue_a}_tracks": _top_tracks(indices_a),
                f"top_{tissue_b}_tracks": _top_tracks(indices_b),
            }

        primary_head = next(
            (h for h in differential_by_head if "fold_change_a_over_b" in differential_by_head[h]),
            None,
        )
        primary = differential_by_head.get(primary_head or "", {})
        viz_indices = (
            [t["track_index"] for t in primary.get(f"top_{tissue_a}_tracks", [])]
            + [t["track_index"] for t in primary.get(f"top_{tissue_b}_tracks", [])]
        )[:top_k]

        return _stash_viz(
            self.session_context,
            {
                "gene": gene,
                "locus": locus,
                "tissue_a": tissue_a,
                "tissue_b": tissue_b,
                "heads": heads,
                "differential_by_head": differential_by_head,
                "summary": (
                    f"Differential regulation of {gene_symbol}: {tissue_a} vs {tissue_b}. "
                    f"Analyzed {len([h for h in differential_by_head if 'fold_change_a_over_b' in differential_by_head[h]])} heads."
                ),
                "viz_spec": {
                    "type": "igv_tracks",
                    "prediction_id": pred["prediction_id"],
                    "locus": locus,
                    "head": primary_head or heads[0],
                    "track_indices": viz_indices,
                } if primary_head else None,
            },
        )
