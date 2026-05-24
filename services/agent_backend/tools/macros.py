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
    use_cache: bool = True,
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
    if use_cache and session_context is not None:
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


def _rank_expression_tracks(
    arr: np.ndarray,
    metadata: list[dict[str, Any]],
    keywords: list[str] | None,
    top_k: int,
    bottom_k: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str | None]:
    """Filter by tissue first, then rank within the filtered set.

    Unlike _rank_tracks + _filter_by_tissue (which filters a globally pre-ranked
    list), this ensures low expressors within a requested tissue are visible even
    when they don't make the global top-k. Returns (high, low, stats, note).

    `low` contains the `bottom_k` lowest-signal tracks in ascending order of
    signal (lowest first). High and low never overlap.
    """
    axes = tuple(range(arr.ndim - 1))
    means = arr.mean(axis=axes)

    idx_to_meta = {m["track_index"]: m for m in metadata}
    all_tracks: list[dict[str, Any]] = []
    for i in range(len(means)):
        track: dict[str, Any] = {"track_index": i, "mean_signal": float(means[i])}
        meta = idx_to_meta.get(i)
        if meta:
            for field in _ANNOTATION_FIELDS:
                if meta.get(field):
                    track[field] = meta[field]
        all_tracks.append(track)

    if not metadata:
        sorted_all = sorted(all_tracks, key=lambda t: t["mean_signal"], reverse=True)
        low: list[dict[str, Any]] = []
        if bottom_k and len(sorted_all) > top_k:
            low_start = max(top_k, len(sorted_all) - bottom_k)
            low = list(reversed(sorted_all[low_start:]))
        return (
            sorted_all[:top_k],
            low,
            {},
            "Track metadata unavailable; returning top/bottom tracks by signal only.",
        )

    note: str | None = None
    if keywords:
        kw = [k.lower() for k in keywords]
        filtered = [
            t for t in all_tracks
            if any(
                k in " ".join(str(t.get(f, "") or "") for f in _TISSUE_FIELDS).lower()
                for k in kw
            )
        ]
        if not filtered:
            filtered = all_tracks
            note = (
                f"No tracks matched tissue keywords {keywords}; "
                "showing global expression ranking."
            )
    else:
        filtered = all_tracks

    sorted_desc = sorted(filtered, key=lambda t: t["mean_signal"], reverse=True)
    signals = [t["mean_signal"] for t in sorted_desc]

    high = sorted_desc[:top_k]
    low = []
    if bottom_k and len(sorted_desc) > top_k:
        low_start = max(top_k, len(sorted_desc) - bottom_k)
        low = list(reversed(sorted_desc[low_start:]))

    stats: dict[str, Any] = {
        "max": float(signals[0]) if signals else 0.0,
        "min": float(signals[-1]) if signals else 0.0,
        "mean": float(np.mean(signals)) if signals else 0.0,
        "n_tracks": len(sorted_desc),
    }
    if high and low:
        denom = max(low[0]["mean_signal"], 1e-9)
        stats["high_to_low_ratio"] = round(float(high[0]["mean_signal"] / denom), 2)

    return high, low, stats, note


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
        p_data = {
            "prediction_id": pred["prediction_id"],
            "locus": locus,
            "arrays": decoded,
            "resolution": resolution,
            "organism": organism,
        }
        session_context.last_prediction = p_data
        session_context.predictions[pred["prediction_id"]] = p_data
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


class AnalyzeGeneExpression(SessionAwareTool):
    name = "analyze_gene_expression"
    description = (
        "Find which cell types or tissues express a gene highly or lowly, using "
        "RNA-seq and CAGE predictions from AlphaGenome. Looks up the gene, runs "
        "AlphaGenome on a window centered at the gene body, and returns the "
        "highest- and lowest-expressing cell types within the requested tissue "
        "group (or globally if no tissue filter is given). Also returns signal "
        "statistics (max, min, mean, high-to-low ratio) the agent can use to "
        "interpret expression breadth and specificity. Renders all tracks in the "
        "UI. Use this for questions like 'what cell types express gene X', "
        "'is gene X brain-specific', 'where is gene X highly vs lowly expressed'."
    )
    inputs = {
        "gene_symbol": {
            "type": "string",
            "description": "HGNC gene symbol, e.g. 'NRXN1', 'GAPDH', 'TP53'.",
        },
        "heads": {
            "type": "array",
            "description": "Expression heads to query. Default: ['rna_seq', 'cage']. Allowed: 'rna_seq', 'cage', 'procap'.",
            "nullable": True,
        },
        "tissue_keywords": {
            "type": "array",
            "description": (
                "Substrings to restrict tracks to a tissue or cell-type group "
                "(e.g. ['brain', 'neuron', 'cortex']). When provided, high/low "
                "rankings are computed within the matched tracks only, so you see "
                "which cell types within that group express the gene most/least. "
                "Omit to rank globally across all cell types."
            ),
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Number of highest-expressing cell types to return per head. Default 5.",
            "nullable": True,
        },
        "bottom_k": {
            "type": "integer",
            "description": "Number of lowest-expressing cell types to return per head. Default 5. Set to 0 to skip low-expression tracks.",
            "nullable": True,
        },
        "organism": {
            "type": "string",
            "description": "'human' or 'mouse'. Default 'human'.",
            "nullable": True,
        },
    }
    output_type = "object"

    _ALLOWED_HEADS = {"rna_seq", "cage", "procap"}

    def forward(
        self,
        gene_symbol: str,
        heads: list[str] | None = None,
        tissue_keywords: list[str] | None = None,
        top_k: int = 5,
        bottom_k: int = 5,
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome
        heads = [h for h in (heads or ["rna_seq", "cage"]) if h in self._ALLOWED_HEADS] or ["rna_seq", "cage"]

        try:
            gene = _ensembl_lookup(gene_symbol, organism)
        except LookupError as e:
            return {"error": str(e)}

        gene_center = (gene["start"] + gene["end"]) // 2
        locus = _window_around(gene["chrom"], gene_center, cfg.max_input_length)
        pred = _predict(locus, None, heads, cfg.default_resolution, organism, self.session_context)
        decoded = _store_prediction(self.session_context, pred, locus, cfg.default_resolution, organism)

        expression_by_head: dict[str, dict[str, Any]] = {}
        notes: list[str] = []
        for head in heads:
            if head not in decoded:
                continue
            metadata = _fetch_head_metadata(head, organism)
            high, low, stats, note = _rank_expression_tracks(
                decoded[head], metadata, tissue_keywords, top_k, bottom_k
            )
            expression_by_head[head] = {
                "high_expressing": high,
                "low_expressing": low,
                "signal_stats": stats,
            }
            if note:
                notes.append(f"[{head}] {note}")

        primary_head = next((h for h in heads if h in expression_by_head), heads[0])
        primary = expression_by_head.get(primary_head, {})
        viz_indices = (
            [t["track_index"] for t in primary.get("high_expressing", [])]
            + [t["track_index"] for t in primary.get("low_expressing", [])]
        )

        return _stash_viz(self.session_context, {
            "gene": gene,
            "window": locus,
            "heads": heads,
            "expression_by_head": expression_by_head,
            "notes": notes or None,
            "viz_spec": {
                "type": "igv_tracks",
                "prediction_id": pred["prediction_id"],
                "locus": locus,
                "head": primary_head,
                "track_indices": viz_indices,
            },
        })


class AnalyzeGeneArbitraryTracks(SessionAwareTool):
    name = "analyze_gene_arbitrary_tracks"
    description = (
        "Analyze any AlphaGenome track type(s) around a gene. Takes a gene symbol, one "
        "or more AlphaGenome head names, and optional tissue keywords; looks up the gene, "
        "runs AlphaGenome on a window centered at the gene body, ranks the top (and "
        "optionally bottom) tracks per head, filters by tissue if requested, and renders "
        "the result. Use this when the user asks about a specific AlphaGenome head that "
        "is not covered by the other analysis tools (e.g. 'atac', 'dnase', 'chip_histone', "
        "'splice_sites'). For expression questions prefer analyze_gene_expression. "
        "Available heads: atac, dnase, procap, cage, rna_seq, chip_tf, chip_histone, "
        "splice_sites, splice_junctions, splice_site_usage."
    )
    inputs = {
        "gene_symbol": {
            "type": "string",
            "description": "HGNC gene symbol, e.g. 'BRCA1', 'TP53', 'MYC'.",
        },
        "heads": {
            "type": "array",
            "description": (
                "One or more AlphaGenome head names to query. Required. "
                "Allowed: atac, dnase, procap, cage, rna_seq, chip_tf, chip_histone, "
                "splice_sites, splice_junctions, splice_site_usage."
            ),
        },
        "tissue_keywords": {
            "type": "array",
            "description": "Optional substrings to match against track cell type. If omitted, returns top tracks across all tissues.",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Number of highest-signal tracks to return per head. Default 10.",
            "nullable": True,
        },
        "bottom_k": {
            "type": "integer",
            "description": "Number of lowest-signal tracks to return per head. Default 0 (omitted). Set >0 to also see low-signal tracks.",
            "nullable": True,
        },
        "organism": {
            "type": "string",
            "description": "'human' or 'mouse'. Default 'human'.",
            "nullable": True,
        },
    }
    output_type = "object"

    _VALID_HEADS = {
        "atac", "dnase", "procap", "cage", "rna_seq",
        "chip_tf", "chip_histone", "splice_sites", "splice_junctions", "splice_site_usage",
    }

    def forward(
        self,
        gene_symbol: str,
        heads: list[str],
        tissue_keywords: list[str] | None = None,
        top_k: int = 10,
        bottom_k: int = 0,
        organism: str = "human",
    ) -> dict[str, Any]:
        cfg = load_settings().alphagenome

        invalid = [h for h in heads if h not in self._VALID_HEADS]
        if invalid:
            return {
                "error": f"Unknown head(s): {invalid}. Valid heads: {sorted(self._VALID_HEADS)}."
            }

        try:
            gene = _ensembl_lookup(gene_symbol, organism)
        except LookupError as e:
            return {"error": str(e)}

        gene_center = (gene["start"] + gene["end"]) // 2
        locus = _window_around(gene["chrom"], gene_center, cfg.max_input_length)
        pred = _predict(locus, None, heads, cfg.default_resolution, organism, self.session_context)
        decoded = _store_prediction(self.session_context, pred, locus, cfg.default_resolution, organism)

        by_head: dict[str, dict[str, Any]] = {}
        notes: list[str] = []
        for head in heads:
            if head not in decoded:
                continue
            metadata = _fetch_head_metadata(head, organism)
            high, low, stats, note = _rank_expression_tracks(
                decoded[head], metadata, tissue_keywords, top_k, bottom_k
            )
            entry: dict[str, Any] = {"top_tracks": high, "signal_stats": stats}
            if low:
                entry["low_tracks"] = low
            by_head[head] = entry
            if note:
                notes.append(f"[{head}] {note}")

        primary_head = next((h for h in heads if h in by_head), heads[0])
        primary = by_head.get(primary_head, {})
        viz_indices = (
            [t["track_index"] for t in primary.get("top_tracks", [])]
            + [t["track_index"] for t in primary.get("low_tracks", [])]
        )

        return _stash_viz(self.session_context, {
            "gene": gene,
            "window": locus,
            "heads": heads,
            "tracks_by_head": by_head,
            "notes": notes or None,
            "viz_spec": {
                "type": "igv_tracks",
                "prediction_id": pred["prediction_id"],
                "locus": locus,
                "head": primary_head,
                "track_indices": viz_indices,
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


# ---------------------------------------------------------------------------
# Phase 2 — Public API knowledge macros
# ---------------------------------------------------------------------------


class AnalyzeVariantClinical(SessionAwareTool):
    name = "analyze_variant_clinical"
    description = (
        "Comprehensive clinical analysis of variants in a gene. Queries ClinVar for "
        "pathogenicity classifications, gnomAD for population allele frequencies and "
        "gene-level constraint scores (pLI, LOEUF), and optionally runs AlphaGenome "
        "variant-effect prediction. Use for 'what pathogenic variants are in gene X' "
        "or 'is gene X under evolutionary constraint' questions."
    )
    inputs = {
        "gene_symbol": {
            "type": "string",
            "description": "HGNC gene symbol, e.g. 'BRCA1', 'TP53'.",
        },
        "variant_hgvs": {
            "type": "string",
            "description": "Optional HGVS string for a specific variant to score with AlphaGenome (e.g. 'chr17:g.43044295A>G').",
            "nullable": True,
        },
        "clinical_significance": {
            "type": "string",
            "description": "Filter ClinVar by significance (e.g. 'pathogenic'). Optional.",
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
        variant_hgvs: str | None = None,
        clinical_significance: str | None = None,
        organism: str = "human",
    ) -> dict[str, Any]:
        from .clinvar import QueryClinvar
        from .gnomad import QueryGnomad

        clinvar_result = QueryClinvar(session_context=self.session_context).forward(
            gene_symbol=gene_symbol,
            clinical_significance=clinical_significance,
        )
        gnomad_result = QueryGnomad(session_context=self.session_context).forward(
            gene_symbol=gene_symbol,
        )

        result: dict[str, Any] = {
            "gene_symbol": gene_symbol,
            "clinvar": clinvar_result,
            "gnomad": gnomad_result,
        }

        if variant_hgvs:
            ag_result = AnalyzeVariantEffect(session_context=self.session_context).forward(
                variant_hgvs=variant_hgvs,
                organism=organism,
            )
            result["alphagenome_variant_effect"] = ag_result

        pathogenic_count = clinvar_result.get("pathogenic_count", 0)
        pLI = (gnomad_result.get("constraint") or {}).get("pLI")
        loeuf = (gnomad_result.get("constraint") or {}).get("loeuf")
        constraint_str = ""
        if pLI is not None:
            constraint_str = f"pLI={pLI:.2f}"
        if loeuf is not None:
            constraint_str += f", LOEUF={loeuf:.2f}" if constraint_str else f"LOEUF={loeuf:.2f}"
        result["summary"] = (
            f"{gene_symbol}: {pathogenic_count} pathogenic/likely-pathogenic ClinVar entries. "
            + (f"gnomAD constraint: {constraint_str}." if constraint_str else "")
            + (" " + gnomad_result.get("interpretation", "") if gnomad_result.get("interpretation") else "")
        ).strip()

        return result


class AnalyzeGwasAssociations(SessionAwareTool):
    name = "analyze_gwas_associations"
    description = (
        "Find GWAS Catalog trait associations for a gene. Returns diseases and phenotypes "
        "associated with variants near the gene, with p-values and risk alleles. Use when "
        "the user asks about disease associations, GWAS hits, or which traits are linked to "
        "a gene or genomic region."
    )
    inputs = {
        "gene_symbol": {
            "type": "string",
            "description": "HGNC gene symbol, e.g. 'TCF7L2', 'FTO', 'APOE'.",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of associations to retrieve (default 30).",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(self, gene_symbol: str, max_results: int = 30) -> dict[str, Any]:
        from .gwas import QueryGwasCatalog

        gwas_result = QueryGwasCatalog(session_context=self.session_context).forward(
            gene_symbol=gene_symbol,
            max_results=max_results,
        )

        top_traits = gwas_result.get("top_traits", [])
        trait_summary = (
            "Top associated traits: " + "; ".join(f"{t['trait']} (n={t['association_count']})" for t in top_traits[:5])
            if top_traits else "No GWAS associations found."
        )
        return {
            "gene_symbol": gene_symbol,
            "gwas": gwas_result,
            "summary": f"{gene_symbol} GWAS summary — {trait_summary}",
        }


class AnalyzeKnownRegulatoryElements(SessionAwareTool):
    name = "analyze_known_regulatory_elements"
    description = (
        "Combine ENCODE cCRE annotations with AlphaGenome chromatin predictions for a "
        "genomic region. Returns known regulatory elements (promoters, enhancers, CTCF sites) "
        "alongside predicted accessibility and histone mark profiles. Use when the user asks "
        "what regulatory elements are known in a region, or wants experimental + predicted "
        "chromatin data together."
    )
    inputs = {
        "locus": {
            "type": "string",
            "description": "Genomic locus 'chrN:start-end', e.g. 'chr17:43044295-43125483'.",
        },
        "organism": {
            "type": "string",
            "description": "'human' or 'mouse'. Default 'human'.",
            "nullable": True,
        },
        "include_alphagenome": {
            "type": "boolean",
            "description": "Whether to also run AlphaGenome chromatin prediction. Default true.",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(
        self,
        locus: str,
        organism: str = "human",
        include_alphagenome: bool = True,
    ) -> dict[str, Any]:
        from .encode import QueryEncodeElements

        encode_result = QueryEncodeElements(session_context=self.session_context).forward(
            locus=locus,
            organism=organism,
        )

        result: dict[str, Any] = {
            "locus": locus,
            "organism": organism,
            "encode_ccres": encode_result,
        }

        if include_alphagenome:
            ag_result = AnalyzeRegionRegulation(session_context=self.session_context).forward(
                locus=locus,
                organism=organism,
            )
            result["alphagenome_chromatin"] = ag_result
            if "viz_spec" in ag_result:
                result["viz_spec"] = ag_result["viz_spec"]

        n_elements = encode_result.get("total_elements", 0)
        class_summary = encode_result.get("class_summary", {})
        class_str = "; ".join(f"{k}: {v}" for k, v in class_summary.items()) if class_summary else "no elements"
        result["summary"] = (
            f"Found {n_elements} ENCODE cCREs in {locus} ({class_str}). "
            + (encode_result.get("summary", "") if not class_summary else "")
        ).strip()
        return _stash_viz(self.session_context, result)


class AnalyzeGtexExpression(SessionAwareTool):
    name = "analyze_gtex_expression"
    description = (
        "Query GTEx for experimental RNA-seq expression of a gene across human tissues. "
        "Returns median TPM per tissue, highest and lowest expressing tissues, and eQTL "
        "summary. Use when the user asks about tissue expression from experimental data "
        "(as opposed to AlphaGenome-predicted expression). Complements analyze_gene_expression "
        "which uses AlphaGenome predictions."
    )
    inputs = {
        "gene_symbol": {
            "type": "string",
            "description": "HGNC gene symbol, e.g. 'TP53', 'NRXN1', 'ACTB'.",
        },
        "tissue_keywords": {
            "type": "array",
            "description": "Optional tissue substrings to filter results (e.g. ['brain', 'neuron']).",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Number of highest/lowest expressing tissues to highlight (default 5).",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(
        self,
        gene_symbol: str,
        tissue_keywords: list[str] | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        from .gtex import QueryGtexExpression as _QueryGtex

        return _QueryGtex(session_context=self.session_context).forward(
            gene_symbol=gene_symbol,
            tissue_keywords=tissue_keywords,
            top_k=top_k,
        )


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
