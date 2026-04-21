from __future__ import annotations

from langchain_core.tools import StructuredTool

from .langchain_adapter import build_tool_list

# Re-export for any code that still imports the macro classes directly.
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
    # Phase 3 macros
    "AnalyzeSplicing",
    "Analyze3dContacts",
    "AnalyzeDifferentialRegulation",
    "AnalyzeLargeRegion",
    "DesignCrisprGuide",
    # Phase 1 primitives
    "GeneToLocus",
    "PredictTracks",
    "PredictVariantEffect",
    "GetTopTracks",
    "ListTracksByAssay",
    "RenderPanel",
    "ParseHgvs",
    # Phase 2 primitives
    "QueryClinvar",
    "QueryGnomad",
    "QueryGwasCatalog",
    "QueryEncodeElements",
    "QueryGtexExpression",
    "QueryConservation",
    "ScanJasparMotifs",
    # Phase 3 primitives
    "AnalyzeAttribution",
    "OptimizeSequence",
]
