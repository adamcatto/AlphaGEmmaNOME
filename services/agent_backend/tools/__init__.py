from __future__ import annotations

from langchain_core.tools import StructuredTool

from .langchain_adapter import build_tool_list

# Re-export for any code that still imports the macro classes directly.
from .alphagenome import GetTopTracks, PredictTracks, PredictVariantEffect
from .clinvar import QueryClinvar
from .conservation import QueryConservation
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
from .tracks import ListTracksByAssay
from .uploads import UploadSequence
from .variants import ParseHgvs
from .viz import RenderPanel

__all__ = [
    "build_tool_list",
    # Phase 1 macros
    "AnalyzeGeneTfBinding",
    "AnalyzeGeneExpression",
    "AnalyzeGeneArbitraryTracks",
    "AnalyzeRegionRegulation",
    "AnalyzeVariantEffect",
    "UploadSequence",
    # Phase 2 macros
    "AnalyzeVariantClinical",
    "AnalyzeGwasAssociations",
    "AnalyzeKnownRegulatoryElements",
    "AnalyzeGtexExpression",
    # Phase 2 primitives
    "QueryClinvar",
    "QueryGnomad",
    "QueryGwasCatalog",
    "QueryEncodeElements",
    "QueryGtexExpression",
    "QueryConservation",
    "ScanJasparMotifs",
    # Phase 1 primitives
    "GeneToLocus",
    "PredictTracks",
    "PredictVariantEffect",
    "GetTopTracks",
    "ListTracksByAssay",
    "RenderPanel",
    "ParseHgvs",
]
