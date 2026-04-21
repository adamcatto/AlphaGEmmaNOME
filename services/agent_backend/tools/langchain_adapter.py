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


# Phase 2 schemas

class QueryClinvarInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'BRCA1', 'TP53'.")
    max_results: int = Field(20, description="Maximum number of variants to return.")
    clinical_significance: str | None = Field(None, description="Filter by significance: 'pathogenic', 'likely_pathogenic', 'benign', etc.")


class QueryGnomadInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'BRCA1'.")
    reference_genome: str = Field("GRCh38", description="'GRCh38' or 'GRCh37'.")
    include_clinvar_variants: bool = Field(True, description="Whether to include ClinVar variants.")


class QueryGwasCatalogInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'TCF7L2', 'APOE'.")
    max_results: int = Field(25, description="Max number of associations to return.")


class QueryEncodeElementsInput(BaseModel):
    locus: str = Field(description="Genomic locus 'chrN:start-end', e.g. 'chr17:43044295-43125483'.")
    organism: str = Field("human", description="'human' or 'mouse'.")
    max_results: int = Field(50, description="Max elements to return.")


class QueryGtexExpressionInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'TP53', 'BRCA1'.")
    tissue_keywords: list[str] | None = Field(None, description="Optional tissue substrings to filter (e.g. ['brain', 'liver']).")
    dataset_id: str = Field("gtex_v8", description="GTEx dataset ID.")
    top_k: int = Field(5, description="Number of highest/lowest expressing tissues to highlight.")


class QueryConservationInput(BaseModel):
    locus: str = Field(description="Genomic locus 'chrN:start-end', e.g. 'chr17:43044295-43125483'.")
    organism: str = Field("human", description="'human' or 'mouse'.")
    include_per_base: bool = Field(False, description="Whether to include per-base score arrays.")


class ScanJasparMotifsInput(BaseModel):
    sequence: str = Field(description="DNA sequence to scan (A/C/G/T/N). Recommended length: 100–2000 bp.")
    tf_names: list[str] | None = Field(None, description="Optional list of specific TF names to restrict to (e.g. ['CTCF', 'SP1']).")
    threshold: float = Field(0.80, description="Relative score threshold 0–1.")
    tax_group: str = Field("vertebrates", description="Taxonomic group: 'vertebrates', 'insects', 'plants', etc.")


# Phase 3 schemas

class AnalyzeSplicingInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'NRXN1', 'TP53'.")
    tissue_keywords: list[str] | None = Field(None, description="Optional tissue substrings to filter tracks (e.g. ['brain', 'neuron']).")
    top_k: int = Field(5, description="Top tracks per splice head.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class Analyze3dContactsInput(BaseModel):
    gene_symbol: str | None = Field(None, description="HGNC gene symbol. Mutually exclusive with locus.")
    locus: str | None = Field(None, description="Genomic locus 'chrN:start-end'. Mutually exclusive with gene_symbol.")
    top_k: int = Field(10, description="Top contact pairs to return.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class AnalyzeDifferentialRegulationInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'BRCA1', 'TP53'.")
    tissue_a: str = Field(description="First tissue group substring, e.g. 'breast'.")
    tissue_b: str = Field(description="Second tissue group substring, e.g. 'liver'.")
    heads: list[str] | None = Field(None, description="Heads to compare. Default: ['atac', 'chip_tf', 'rna_seq'].")
    top_k: int = Field(5, description="Top differential tracks per head per tissue.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class AnalyzeLargeRegionInput(BaseModel):
    locus: str = Field(description="Genomic locus 'chrN:start-end'. Must span multiple AlphaGenome windows.")
    heads: list[str] | None = Field(None, description="Heads to scan. Default: ['atac', 'chip_histone'].")
    top_k: int = Field(3, description="Top tracks per tile to report.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class DesignCrisprGuideInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'BRCA1', 'TP53'.")
    target_region: str | None = Field(None, description="'tss' (default), 'coding', or a specific locus 'chrN:start-end'.")
    window_bp: int = Field(500, description="Window around target to scan for PAM sites.")
    top_k: int = Field(5, description="Number of top guide candidates to return.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class AnalyzeAttributionInput(BaseModel):
    locus: str = Field(description="Full AlphaGenome input window 'chrN:start-end'.")
    head: str = Field(description="AlphaGenome head to attribute, e.g. 'atac', 'chip_tf'.")
    track_index: int = Field(description="Track index within the head.")
    attribution_region: str | None = Field(None, description="Sub-region for ISM 'chrN:start-end' (≤64 bp recommended).")
    resolution: str = Field("128bp", description="'1bp' or '128bp'.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class OptimizeSequenceInput(BaseModel):
    locus: str = Field(description="Full AlphaGenome input window 'chrN:start-end'.")
    head: str = Field(description="Target AlphaGenome head to maximize, e.g. 'atac', 'cage'.")
    track_index: int = Field(description="Track index within the head.")
    design_region: str | None = Field(None, description="Sub-region to scan for mutations 'chrN:start-end' (≤100 bp recommended).")
    top_k: int = Field(5, description="Number of top candidate mutations to return.")
    organism: str = Field("human", description="'human' or 'mouse'.")


class AnalyzeVariantClinicalInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'BRCA1', 'TP53'.")
    variant_hgvs: str | None = Field(None, description="Optional HGVS string for a specific variant to score with AlphaGenome.")
    clinical_significance: str | None = Field(None, description="Filter ClinVar by significance (e.g. 'pathogenic').")
    organism: str = Field("human", description="'human' or 'mouse'.")


class AnalyzeGwasAssociationsInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'TCF7L2', 'FTO', 'APOE'.")
    max_results: int = Field(30, description="Maximum number of associations to retrieve.")


class AnalyzeKnownRegulatoryElementsInput(BaseModel):
    locus: str = Field(description="Genomic locus 'chrN:start-end'.")
    organism: str = Field("human", description="'human' or 'mouse'.")
    include_alphagenome: bool = Field(True, description="Whether to also run AlphaGenome chromatin prediction.")


class AnalyzeGtexExpressionMacroInput(BaseModel):
    gene_symbol: str = Field(description="HGNC gene symbol, e.g. 'TP53', 'NRXN1', 'ACTB'.")
    tissue_keywords: list[str] | None = Field(None, description="Optional tissue substrings to filter results (e.g. ['brain', 'neuron']).")
    top_k: int = Field(5, description="Number of highest/lowest expressing tissues to highlight.")


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
    from .attribution import AnalyzeAttribution
    from .clinvar import QueryClinvar
    from .comparative import AnalyzeDifferentialRegulation
    from .conservation import QueryConservation
    from .contacts import Analyze3dContacts
    from .crispr import DesignCrisprGuide
    from .encode import QueryEncodeElements
    from .genome_lookup import GeneToLocus
    from .gnomad import QueryGnomad
    from .gtex import QueryGtexExpression
    from .gwas import QueryGwasCatalog
    from .jaspar import ScanJasparMotifs
    from .macros import (
        AnalyzeGeneArbitraryTracks,
        AnalyzeGeneExpression,
        AnalyzeGeneTfBinding,
        AnalyzeGtexExpression,
        AnalyzeGwasAssociations,
        AnalyzeKnownRegulatoryElements,
        AnalyzeRegionRegulation,
        AnalyzeVariantClinical,
        AnalyzeVariantEffect,
    )
    from .sequence_design import OptimizeSequence
    from .splice import AnalyzeSplicing
    from .tiled_scan import AnalyzeLargeRegion
    from .tracks import ListTracksByAssay
    from .uploads import UploadSequence
    from .variants import ParseHgvs
    from .viz import RenderPanel

    _ALL: list[tuple[type, str, type[BaseModel]]] = [
        # --- Phase 1 macros (enabled by default) ---
        (AnalyzeGeneTfBinding, "analyze_gene_tf_binding", AnalyzeGeneTfBindingInput),
        (AnalyzeGeneExpression, "analyze_gene_expression", AnalyzeGeneExpressionInput),
        (AnalyzeGeneArbitraryTracks, "analyze_gene_arbitrary_tracks", AnalyzeGeneArbitraryTracksInput),
        (AnalyzeRegionRegulation, "analyze_region_regulation", AnalyzeRegionRegulationInput),
        (AnalyzeVariantEffect, "analyze_variant_effect", AnalyzeVariantEffectInput),
        (UploadSequence, "upload_sequence", UploadSequenceInput),
        # Phase 2 macros
        (AnalyzeVariantClinical, "analyze_variant_clinical", AnalyzeVariantClinicalInput),
        (AnalyzeGwasAssociations, "analyze_gwas_associations", AnalyzeGwasAssociationsInput),
        (AnalyzeKnownRegulatoryElements, "analyze_known_regulatory_elements", AnalyzeKnownRegulatoryElementsInput),
        (AnalyzeGtexExpression, "analyze_gtex_expression", AnalyzeGtexExpressionMacroInput),
        # Phase 3 macros
        (AnalyzeSplicing, "analyze_splicing", AnalyzeSplicingInput),
        (Analyze3dContacts, "analyze_3d_contacts", Analyze3dContactsInput),
        (AnalyzeDifferentialRegulation, "analyze_differential_regulation", AnalyzeDifferentialRegulationInput),
        (AnalyzeLargeRegion, "analyze_large_region", AnalyzeLargeRegionInput),
        (DesignCrisprGuide, "design_crispr_guide", DesignCrisprGuideInput),
        # --- Phase 1 primitives (disabled by default) ---
        (GeneToLocus, "gene_to_locus", GeneToLocusInput),
        (PredictTracks, "predict_tracks", PredictTracksInput),
        (PredictVariantEffect, "predict_variant_effect", PredictVariantEffectInput),
        (GetTopTracks, "get_top_tracks", GetTopTracksInput),
        (ListTracksByAssay, "list_tracks_by_assay", ListTracksByAssayInput),
        (RenderPanel, "render_panel", RenderPanelInput),
        (ParseHgvs, "parse_hgvs", ParseHgvsInput),
        # Phase 2 primitives
        (QueryClinvar, "query_clinvar", QueryClinvarInput),
        (QueryGnomad, "query_gnomad", QueryGnomadInput),
        (QueryGwasCatalog, "query_gwas_catalog", QueryGwasCatalogInput),
        (QueryEncodeElements, "query_encode_elements", QueryEncodeElementsInput),
        (QueryGtexExpression, "query_gtex_expression", QueryGtexExpressionInput),
        (QueryConservation, "query_conservation", QueryConservationInput),
        (ScanJasparMotifs, "scan_jaspar_motifs", ScanJasparMotifsInput),
        # Phase 3 primitives (ISM-based — slow, disabled by default)
        (AnalyzeAttribution, "analyze_attribution", AnalyzeAttributionInput),
        (OptimizeSequence, "optimize_sequence", OptimizeSequenceInput),
    ]

    cfg = load_settings().tools
    tools: list[StructuredTool] = []
    for macro_cls, name, schema in _ALL:
        tool_cfg = cfg.get(name)
        if tool_cfg and tool_cfg.enabled:
            tools.append(make_tool(macro_cls, name, macro_cls.description, schema))
    return tools
