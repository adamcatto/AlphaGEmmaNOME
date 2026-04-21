"""Wrap existing SessionAwareTool macros as LangChain StructuredTools.

Each tool's core logic lives in macros.py / primitive tool files and is
untouched. This module defines Pydantic input schemas and a factory that
adapts SessionAwareTool.forward() for LangGraph's ToolNode.

Session access: LangGraph's ToolNode injects the RunnableConfig into tools
that declare `config: RunnableConfig` as a parameter. We resolve the session
from config["configurable"]["session_id"] so tools can still read/write
prediction caches, uploads, and last_prediction on the Session object.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from schema import load_settings

from ..sessions import STORE


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

class AnalyzeGeneTfBindingInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'ASPM', 'BRCA1', 'TP53'.")
    tissue_keywords: list[str] | None = Field(None, description="Optional substrings to match against track cell_type (e.g. ['brain','cortex']).")
    top_k: int = Field(10, description="Number of top tracks to return.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class AnalyzeGeneExpressionInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'NRXN1', 'GAPDH', 'TP53'.")
    heads: list[str] | None = Field(None, description="Expression heads to query. Default: ['rna_seq', 'cage']. Allowed: 'rna_seq', 'cage', 'procap'.")
    tissue_keywords: list[str] | None = Field(None, description="Substrings to restrict tracks to a tissue/cell-type group (e.g. ['brain', 'neuron']).")
    top_k: int = Field(5, description="Number of highest-expressing cell types to return per head.")
    bottom_k: int = Field(5, description="Number of lowest-expressing cell types to return per head. Set 0 to skip.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class AnalyzeGeneArbitraryTracksInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'BRCA1', 'TP53', 'MYC'.")
    heads: list[str] = Field(description="One or more AlphaGenome head names. Allowed: atac, dnase, procap, cage, rna_seq, chip_tf, chip_histone, splice_sites, splice_junctions, splice_site_usage.")
    tissue_keywords: list[str] | None = Field(None, description="Optional substrings to match against track cell type.")
    top_k: int = Field(10, description="Number of highest-signal tracks per head.")
    bottom_k: int = Field(0, description="Number of lowest-signal tracks per head. Default 0 (off).")
    organism: str = Field("human", description="'human' or 'mouse'.")


class AnalyzeRegionRegulationInput(BaseModel):
    locus: str = Field(description="Genomic locus in 'chrN:start-end' form.")
    heads: list[str] | None = Field(None, description="AlphaGenome heads to profile. Default: ['atac','dnase','chip_histone'].")
    top_k: int = Field(5, description="Top tracks per head.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class AnalyzeVariantEffectInput(BaseModel):
    variant_hgvs: str = Field(description="HGVS genomic string, e.g. 'chr17:g.43044295A>G'. Only SNVs supported.")
    heads: list[str] | None = Field(None, description="Heads to score. Default: ['atac','chip_tf'].")
    window_bp: int | None = Field(None, description="Window size around the variant. Defaults to max_input_length.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class UploadSequenceInput(BaseModel):
    upload_id: str = Field(description="Upload identifier returned by the /upload endpoint.")


class GeneToLocusInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'BRCA1', 'TP53'.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class PredictTracksInput(BaseModel):
    locus: str | None = Field(None, description="Genomic locus 'chrN:start-end'. Mutually exclusive with sequence.")
    sequence: str | None = Field(None, description="Raw DNA sequence (A/C/G/T/N). Mutually exclusive with locus.")
    heads: list[str] | None = Field(None, description="AlphaGenome output heads to return.")
    resolution: str = Field("128bp", description="'1bp' or '128bp'.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class PredictVariantEffectInput(BaseModel):
    locus: str = Field(description="Surrounding window 'chrN:start-end' containing the variant.")
    variant_position: int = Field(description="1-based genomic coordinate of the variant.")
    ref: str = Field(description="Reference base (A/C/G/T).")
    alt: str = Field(description="Alternate base (A/C/G/T).")
    heads: list[str] = Field(description="Heads to compare.")
    resolution: str = Field("128bp", description="'1bp' or '128bp'.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class GetTopTracksInput(BaseModel):
    head: str = Field(description="Which AlphaGenome head to rank.")
    k: int = Field(5, description="Number of top tracks to return.")


class ListTracksByAssayInput(BaseModel):
    assay: str | None = Field(None, description="Substring of assay_title or target_label (e.g. 'CTCF', 'H3K27ac').")
    tissue: str | None = Field(None, description="Substring of biosample_name / gtex_tissue (e.g. 'brain', 'liver').")
    head: str | None = Field(None, description="Restrict to one head (e.g. 'chip_tf').")
    organism: str = Field("human", description="'human' or 'mouse'.")


class RenderPanelInput(BaseModel):
    panel_type: str = Field(description="One of: 'igv_tracks', 'contact_map', 'splice_arcs', 'variant_delta'.")
    head: str | None = Field(None, description="AlphaGenome head name.")
    track_indices: list[int] | None = Field(None, description="Track indices to render.")


class ParseHgvsInput(BaseModel):
    hgvs: str = Field(description="HGVS string like 'chr17:g.43044295A>G'.")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_tool(
    macro_cls: type,
    name: str,
    description: str,
    args_schema: type[BaseModel],
) -> StructuredTool:
    """Wrap a SessionAwareTool as a LangChain StructuredTool.

    The `config: RunnableConfig` parameter is automatically injected by
    LangChain's ToolNode and excluded from the schema shown to the LLM.
    """
    def _fn(config: RunnableConfig, **kwargs: Any) -> dict[str, Any]:
        session_id = (config.get("configurable") or {}).get("session_id", "")
        session = STORE.get_or_create(session_id)
        return macro_cls(session_context=session).forward(**kwargs)

    _fn.__name__ = name

    return StructuredTool.from_function(
        func=_fn,
        name=name,
        description=description,
        args_schema=args_schema,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def build_tool_list() -> list[StructuredTool]:
    """Return LangChain StructuredTools for every tool enabled in tools.yaml."""
    from .alphagenome import GetTopTracks, PredictTracks, PredictVariantEffect
    from .genome_lookup import GeneToLocus
    from .macros import (
        AnalyzeGeneArbitraryTracks,
        AnalyzeGeneExpression,
        AnalyzeGeneTfBinding,
        AnalyzeRegionRegulation,
        AnalyzeVariantEffect,
    )
    from .tracks import ListTracksByAssay
    from .uploads import UploadSequence
    from .variants import ParseHgvs
    from .viz import RenderPanel

    _ALL: list[tuple[type, str, type[BaseModel]]] = [
        (AnalyzeGeneTfBinding, "analyze_gene_tf_binding", AnalyzeGeneTfBindingInput),
        (AnalyzeGeneExpression, "analyze_gene_expression", AnalyzeGeneExpressionInput),
        (AnalyzeGeneArbitraryTracks, "analyze_gene_arbitrary_tracks", AnalyzeGeneArbitraryTracksInput),
        (AnalyzeRegionRegulation, "analyze_region_regulation", AnalyzeRegionRegulationInput),
        (AnalyzeVariantEffect, "analyze_variant_effect", AnalyzeVariantEffectInput),
        (UploadSequence, "upload_sequence", UploadSequenceInput),
        (GeneToLocus, "gene_to_locus", GeneToLocusInput),
        (PredictTracks, "predict_tracks", PredictTracksInput),
        (PredictVariantEffect, "predict_variant_effect", PredictVariantEffectInput),
        (GetTopTracks, "get_top_tracks", GetTopTracksInput),
        (ListTracksByAssay, "list_tracks_by_assay", ListTracksByAssayInput),
        (RenderPanel, "render_panel", RenderPanelInput),
        (ParseHgvs, "parse_hgvs", ParseHgvsInput),
    ]

    cfg = load_settings().tools
    tools: list[StructuredTool] = []
    for macro_cls, name, schema in _ALL:
        tool_cfg = cfg.get(name)
        if tool_cfg and tool_cfg.enabled:
            tools.append(make_tool(macro_cls, name, macro_cls.description, schema))
    return tools
