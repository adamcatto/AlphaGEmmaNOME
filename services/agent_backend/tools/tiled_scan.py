"""Tiled scanning of large genomic regions exceeding a single AlphaGenome window."""

from __future__ import annotations

import base64
from typing import Any

import numpy as np

from schema import load_settings

from ._base import SessionAwareTool
from .macros import _emit, _predict


def _decode_array(arr_obj: dict) -> np.ndarray:
    raw = base64.b64decode(arr_obj["data_b64"])
    return np.frombuffer(raw, dtype=arr_obj["dtype"]).reshape(arr_obj["shape"])


def _parse_locus(locus: str) -> tuple[str, int, int]:
    chrom, coords = locus.split(":", 1)
    start_s, end_s = coords.split("-", 1)
    return chrom, int(start_s), int(end_s)


class AnalyzeLargeRegion(SessionAwareTool):
    name = "analyze_large_region"
    description = (
        "Analyze the regulatory landscape of a genomic region larger than a single "
        "AlphaGenome input window by tiling predictions across it. Returns per-tile "
        "signal summaries, identifies the highest-signal sub-regions, and reports "
        "dominant tracks. Use for questions about regulatory landscapes across gene "
        "clusters, TADs, or other multi-gene windows."
    )
    inputs = {
        "locus": {
            "type": "string",
            "description": "Genomic locus 'chrN:start-end'. Must be larger than a single AlphaGenome window.",
        },
        "heads": {
            "type": "array",
            "description": "Heads to scan. Default: ['atac', 'chip_histone'].",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Top tracks per tile to report. Default 3.",
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
        heads: list[str] | None = None,
        top_k: int = 3,
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome
        heads = heads or ["atac", "chip_histone"]
        window = cfg.max_input_length
        step = window // 2  # 50 % overlap keeps signal continuous at tile edges

        try:
            chrom, start, end = _parse_locus(locus)
        except Exception:
            return {"error": f"Cannot parse locus {locus!r}. Expected 'chrN:start-end'."}

        region_len = end - start
        if region_len <= window:
            return {
                "error": (
                    f"Region {locus} is ≤{window} bp — use analyze_region_regulation "
                    "for single-window analysis."
                )
            }

        # Build non-overlapping tile list; the last tile is right-anchored to avoid
        # processing beyond the requested region.
        tiles: list[str] = []
        pos = start
        while pos < end:
            tile_end = min(pos + window, end)
            tiles.append(f"{chrom}:{pos}-{tile_end}")
            if tile_end == end:
                break
            pos += step

        _emit(
            self.session_context,
            "progress",
            {
                "stage": "tiled_scan_start",
                "text": f"Tiling {locus} into {len(tiles)} windows ({window // 1000}kb each)…",
            },
        )

        tile_summaries: list[dict] = []
        for tile_i, tile_locus in enumerate(tiles):
            _emit(
                self.session_context,
                "progress",
                {"stage": "tiled_scan_tile", "text": f"Tile {tile_i + 1}/{len(tiles)}: {tile_locus}"},
            )
            try:
                pred = _predict(
                    tile_locus, None, heads, cfg.default_resolution, organism, self.session_context
                )
            except Exception as exc:
                tile_summaries.append({"tile": tile_locus, "error": str(exc)})
                continue

            head_stats: dict[str, Any] = {}
            for arr_obj in pred["arrays"]:
                head_name = arr_obj["head"]
                arr = _decode_array(arr_obj)
                axes = tuple(range(arr.ndim - 1))
                means = arr.mean(axis=axes)
                top_idx = np.argsort(-means)[:top_k].tolist()
                head_stats[head_name] = {
                    "max_signal": round(float(means.max()), 4),
                    "mean_signal": round(float(means.mean()), 4),
                    "top_track_indices": [int(i) for i in top_idx],
                }

            tile_summaries.append(
                {
                    "tile": tile_locus,
                    "prediction_id": pred["prediction_id"],
                    "head_stats": head_stats,
                }
            )

        # Rank tiles by max signal across all requested heads
        scored = [
            t for t in tile_summaries if "head_stats" in t
        ]
        scored.sort(
            key=lambda t: max(
                t["head_stats"].get(h, {}).get("max_signal", 0.0) for h in heads
            ),
            reverse=True,
        )
        high_signal_tiles = scored[:5]

        return {
            "locus": locus,
            "n_tiles": len(tiles),
            "window_size_bp": window,
            "step_bp": step,
            "heads": heads,
            "tile_summaries": tile_summaries,
            "high_signal_tiles": high_signal_tiles,
            "summary": (
                f"Scanned {region_len // 1000}kb region in {len(tiles)} tiles. "
                + (
                    f"Highest signal tile: {high_signal_tiles[0]['tile']}."
                    if high_signal_tiles
                    else "No successful tiles."
                )
            ),
        }
