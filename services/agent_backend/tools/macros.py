"""High-level workflow tools.

These encapsulate the gene_to_locus → predict_tracks → get_top_tracks →
render_panel chain that a small LLM can't reliably plan on its own. The agent
picks ONE macro per question; no multi-step chaining required.

The low-level primitives (predict_tracks, get_top_tracks, etc.) still exist in
this package and can be re-enabled via config/tools.yaml for power-user flows.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import numpy as np

from schema import load_settings

from ._base import SessionAwareTool, http_client


def _emit(session_context: Any, event: str, payload: dict[str, Any]) -> None:
    """Push an SSE event via the session's bus if one is attached.

    The bus is set by the /chat handler for the duration of a run; macros that
    run outside a chat context (tests, scripts) silently no-op.
    """
    bus = getattr(session_context, "bus", None) if session_context is not None else None
    if bus is not None:
        try:
            bus.emit(event, payload)
        except Exception:
            pass


def _svc_url() -> str:
    return load_settings().alphagenome.service_url


def _decode(arr: dict) -> np.ndarray:
    raw = base64.b64decode(arr["data_b64"])
    return np.frombuffer(raw, dtype=arr["dtype"]).reshape(arr["shape"])


def _ensembl_lookup(gene_symbol: str, organism: str) -> dict[str, Any]:
    cfg = load_settings().ensembl
    species = "human" if organism == "human" else "mouse"
    url = f"{cfg.rest_base_url}/lookup/symbol/{species}/{gene_symbol}"
    r = http_client().get(
        url,
        headers={"Accept": "application/json"},
        timeout=cfg.request_timeout_seconds,
    )
    if r.status_code == 404:
        raise LookupError(f"Gene {gene_symbol!r} not found in Ensembl ({species}).")
    r.raise_for_status()
    d = r.json()
    strand = "+" if d.get("strand") == 1 else "-"
    tss = d["start"] if strand == "+" else d["end"]
    return {
        "gene_symbol": gene_symbol,
        "ensembl_id": d["id"],
        "chrom": f"chr{d['seq_region_name']}",
        "start": d["start"],
        "end": d["end"],
        "strand": strand,
        "tss": tss,
    }


def _window_around(chrom: str, center: int, window_bp: int) -> str:
    half = window_bp // 2
    start = max(1, center - half)
    end = center + half
    return f"{chrom}:{start}-{end}"


def _cache_key(body: dict[str, Any]) -> tuple:
    return (
        body.get("locus"),
        body.get("sequence"),
        tuple(body.get("heads") or ()),
        body.get("resolution"),
        body.get("organism"),
    )


def _predict(
    locus: str | None,
    sequence: str | None,
    heads: list[str],
    resolution: str,
    organism: str,
    session_context: Any = None,
) -> dict[str, Any]:
    body = {
        "locus": locus,
        "sequence": sequence,
        "heads": heads,
        "resolution": resolution,
        "organism": organism,
    }
    # Qwen3-4b sometimes re-issues the same tool call after a parse failure; avoid
    # paying for a second AlphaGenome forward pass when the args are identical.
    cache = None
    key = _cache_key(body)
    if session_context is not None:
        cache = getattr(session_context, "predict_cache", None)
        if cache is None:
            cache = {}
            session_context.predict_cache = cache
        if key in cache:
            _emit(
                session_context,
                "progress",
                {"stage": "alphagenome_cached", "text": "Reusing previous AlphaGenome result (same args)."},
            )
            return cache[key]

    target = locus if locus is not None else f"uploaded sequence ({len(sequence or '')} bp)"
    _emit(
        session_context,
        "progress",
        {"stage": "alphagenome_start", "text": f"Running AlphaGenome on {target} for heads={heads}…"},
    )
    t0 = time.time()
    r = http_client().post(f"{_svc_url()}/predict", json=body)
    elapsed = time.time() - t0
    if r.status_code >= 400:
        _emit(
            session_context,
            "progress",
            {"stage": "alphagenome_error", "text": f"AlphaGenome failed ({r.status_code}) after {elapsed:.1f}s"},
        )
        raise RuntimeError(f"alphagenome_svc /predict {r.status_code}: {r.text}")
    _emit(
        session_context,
        "progress",
        {"stage": "alphagenome_done", "text": f"AlphaGenome forward pass completed in {elapsed:.1f}s"},
    )
    result = r.json()
    if cache is not None:
        cache[key] = result
        # Bound cache so it doesn't grow unbounded across a long session.
        if len(cache) > 8:
            oldest = next(iter(cache))
            cache.pop(oldest, None)
    return result


def _rank_tracks(arr: np.ndarray, k: int) -> list[dict[str, Any]]:
    axes = tuple(range(arr.ndim - 1))
    means = arr.mean(axis=axes)
    order = np.argsort(-means)[:k]
    return [{"track_index": int(i), "mean_signal": float(means[i])} for i in order]


def _fetch_head_metadata(head: str, organism: str) -> list[dict[str, Any]]:
    try:
        r = http_client().get(
            f"{_svc_url()}/tracks",
            params={"head": head, "organism": organism},
        )
        r.raise_for_status()
        return r.json().get("tracks", [])
    except Exception:
        return []


_TISSUE_FIELDS = ("biosample_name", "gtex_tissue", "gtex_tissue_group")
_ANNOTATION_FIELDS = (
    "target_label",
    "assay_title",
    "biosample_name",
    "biosample_type",
    "gtex_tissue",
    "gtex_tissue_group",
    "name",
)


def _filter_by_tissue(
    ranked: list[dict[str, Any]],
    metadata: list[dict[str, Any]],
    keywords: list[str] | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Annotate ranked tracks with metadata and filter by tissue keywords.

    Tissue match is a case-insensitive substring against biosample_name,
    gtex_tissue, or gtex_tissue_group. Returns (tracks, note); `note` is
    non-null when metadata is missing or the filter eliminates every track.
    """
    idx_to_meta = {m["track_index"]: m for m in metadata}
    for t in ranked:
        meta = idx_to_meta.get(t["track_index"])
        if meta:
            for field in _ANNOTATION_FIELDS:
                if meta.get(field):
                    t[field] = meta[field]

    if not metadata:
        return ranked, "Track metadata unavailable; returning top tracks by signal only."

    if not keywords:
        return ranked, None

    kw = [k.lower() for k in keywords]
    hits = []
    for t in ranked:
        haystack = " ".join(str(t.get(f, "") or "") for f in _TISSUE_FIELDS).lower()
        if any(k in haystack for k in kw):
            hits.append(t)

    if not hits:
        return ranked, (
            f"No tracks matched tissue keywords {keywords}. "
            "Returning top tracks by signal across all tissues."
        )
    return hits, None


def _stash_viz(session_context: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Copy the result's viz_spec onto the session so the SSE step_callback can
    surface it as its own event. smolagents stringifies tool outputs into
    observations, which loses the dict structure; this gives the callback a
    typed hand-off."""
    if session_context is not None and isinstance(result.get("viz_spec"), dict):
        session_context.pending_viz_spec = result["viz_spec"]
    return result


def _store_prediction(
    session_context: Any,
    pred: dict[str, Any],
    locus: str | None,
    resolution: str,
    organism: str,
) -> dict[str, np.ndarray]:
    decoded: dict[str, np.ndarray] = {}
    for arr_obj in pred["arrays"]:
        decoded[arr_obj["head"]] = _decode(arr_obj)
    if session_context is not None:
        session_context.last_prediction = {
            "prediction_id": pred["prediction_id"],
            "locus": locus,
            "arrays": decoded,
            "resolution": resolution,
            "organism": organism,
        }
    return decoded


class AnalyzeGeneTfBinding(SessionAwareTool):
    name = "analyze_gene_tf_binding"
    description = (
        "Find transcription-factor binding predictions around a gene. Takes a single "
        "gene symbol and optional tissue keywords; internally looks up the gene's "
        "coordinates, runs AlphaGenome on a window centered at the TSS, ranks the top "
        "TF ChIP tracks, filters by tissue if requested, and renders them in the UI. "
        "Use this for any 'where do TFs bind near gene X' style question."
    )
    inputs = {
        "gene_symbol": {
            "type": "string",
            "description": "HGNC gene symbol, e.g. 'ASPM', 'BRCA1', 'TP53'.",
        },
        "tissue_keywords": {
            "type": "array",
            "description": "Optional substrings to match against track cell_type (e.g. ['brain','cortex','neural']). If omitted, returns top tracks across all tissues.",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Number of top tracks to return. Default 10.",
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
        top_k: int = 10,
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome
        try:
            gene = _ensembl_lookup(gene_symbol, organism)
        except LookupError as e:
            return {"error": str(e)}

        gene_center = (gene["start"] + gene["end"]) // 2
        locus = _window_around(gene["chrom"], gene_center, cfg.max_input_length)
        pred = _predict(locus, None, ["chip_tf"], cfg.default_resolution, organism, self.session_context)
        decoded = _store_prediction(self.session_context, pred, locus, cfg.default_resolution, organism)

        ranked = _rank_tracks(decoded["chip_tf"], max(top_k * 3, top_k))
        metadata = _fetch_head_metadata("chip_tf", organism)
        tracks, note = _filter_by_tissue(ranked, metadata, tissue_keywords)
        tracks = tracks[:top_k]

        return _stash_viz(self.session_context, {
            "gene": gene,
            "window": locus,
            "head": "chip_tf",
            "top_tracks": tracks,
            "note": note,
            "viz_spec": {
                "type": "igv_tracks",
                "prediction_id": pred["prediction_id"],
                "locus": locus,
                "head": "chip_tf",
                "track_indices": [t["track_index"] for t in tracks],
            },
        })


class AnalyzeRegionRegulation(SessionAwareTool):
    name = "analyze_region_regulation"
    description = (
        "Profile the regulatory landscape of a genomic region. Runs AlphaGenome across "
        "chromatin-accessibility and histone-mark heads and returns the top tracks per "
        "head, plus an IGV panel. Use for 'what regulatory elements are in chrN:start-end' "
        "style questions."
    )
    inputs = {
        "locus": {
            "type": "string",
            "description": "Genomic locus in 'chrN:start-end' form.",
        },
        "heads": {
            "type": "array",
            "description": "Which AlphaGenome heads to profile. Default: ['atac','dnase','chip_histone'].",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Top tracks per head. Default 5.",
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
        top_k: int = 5,
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome
        heads = heads or ["atac", "dnase", "chip_histone"]
        pred = _predict(locus, None, heads, cfg.default_resolution, organism, self.session_context)
        decoded = _store_prediction(self.session_context, pred, locus, cfg.default_resolution, organism)

        by_head: dict[str, list[dict[str, Any]]] = {}
        notes: list[str] = []
        for head in heads:
            if head not in decoded:
                continue
            ranked = _rank_tracks(decoded[head], top_k)
            metadata = _fetch_head_metadata(head, organism)
            tracks, note = _filter_by_tissue(ranked, metadata, None)
            by_head[head] = tracks
            if note:
                notes.append(f"[{head}] {note}")

        primary_head = heads[0]
        return _stash_viz(self.session_context, {
            "locus": locus,
            "heads": heads,
            "top_tracks_by_head": by_head,
            "notes": notes,
            "viz_spec": {
                "type": "igv_tracks",
                "prediction_id": pred["prediction_id"],
                "locus": locus,
                "head": primary_head,
                "track_indices": [t["track_index"] for t in by_head.get(primary_head, [])],
            },
        })


class AnalyzeVariantEffect(SessionAwareTool):
    name = "analyze_variant_effect"
    description = (
        "Predict the functional impact of a single-nucleotide variant. Accepts an HGVS "
        "genomic string like 'chr17:g.43044295A>G'. Runs AlphaGenome twice (reference vs "
        "alternate sequence) on a window around the variant, returns per-head deltas, and "
        "renders a variant-delta panel. Use for 'what's the effect of variant X' questions."
    )
    inputs = {
        "variant_hgvs": {
            "type": "string",
            "description": "HGVS genomic string, e.g. 'chr17:g.43044295A>G'. Only SNVs supported.",
        },
        "heads": {
            "type": "array",
            "description": "Heads to score. Default: ['atac','chip_tf'].",
            "nullable": True,
        },
        "window_bp": {
            "type": "integer",
            "description": "Window size around the variant. Defaults to alphagenome.max_input_length.",
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
        variant_hgvs: str,
        heads: list[str] | None = None,
        window_bp: int | None = None,
        organism: str = "human",
    ) -> dict[str, Any]:
        import re

        m = re.match(r"^(chr[\w]+):g\.(\d+)([ACGT])>([ACGT])$", variant_hgvs)
        if not m:
            return {"error": f"Could not parse {variant_hgvs!r} as a genomic SNV (expected e.g. 'chr17:g.43044295A>G')."}
        chrom, pos, ref, alt = m.group(1), int(m.group(2)), m.group(3), m.group(4)

        cfg = load_settings().alphagenome
        heads = heads or ["atac", "chip_tf"]
        window_bp = window_bp or cfg.max_input_length
        locus = _window_around(chrom, pos, window_bp)

        ref_pred = _predict(locus, None, heads, cfg.default_resolution, organism, self.session_context)

        from .._variant_utils import apply_snv_to_sequence

        alt_sequence = apply_snv_to_sequence(http_client(), _svc_url(), locus, pos, ref, alt)
        alt_pred = _predict(None, alt_sequence, heads, cfg.default_resolution, organism, self.session_context)

        deltas = []
        for ref_obj, alt_obj in zip(ref_pred["arrays"], alt_pred["arrays"]):
            ref_arr = _decode(ref_obj)
            alt_arr = _decode(alt_obj)
            delta = alt_arr - ref_arr
            track_axis = tuple(range(delta.ndim - 1))
            per_track_abs = np.abs(delta).mean(axis=track_axis)
            deltas.append(
                {
                    "head": ref_obj["head"],
                    "l2": float(np.linalg.norm(delta)),
                    "max_abs_delta": float(np.max(np.abs(delta))),
                    "top_track_index": int(np.argmax(per_track_abs)),
                }
            )

        _store_prediction(self.session_context, ref_pred, locus, cfg.default_resolution, organism)

        return _stash_viz(self.session_context, {
            "variant": variant_hgvs,
            "locus": locus,
            "deltas": deltas,
            "viz_spec": {
                "type": "variant_delta",
                "prediction_id": ref_pred["prediction_id"],
                "locus": locus,
                "head": heads[0],
                "variant_position": pos,
            },
        })
