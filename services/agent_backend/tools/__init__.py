from __future__ import annotations

from schema import load_settings
from smolagents import Tool

from .alphagenome import GetTopTracks, PredictTracks, PredictVariantEffect
from .genome_lookup import GeneToLocus
from .macros import AnalyzeGeneTfBinding, AnalyzeRegionRegulation, AnalyzeVariantEffect
from .tracks import ListTracksByAssay
from .uploads import UploadSequence
from .variants import ParseHgvs
from .viz import RenderPanel


_ALL: dict[str, type[Tool]] = {
    # Macros — the default agent-facing surface. Each encapsulates a full
    # multi-step workflow so a small LLM only has to pick one tool per question.
    "analyze_gene_tf_binding": AnalyzeGeneTfBinding,
    "analyze_region_regulation": AnalyzeRegionRegulation,
    "analyze_variant_effect": AnalyzeVariantEffect,
    # Uploads still need their own primitive — no macro subsumes them yet.
    "upload_sequence": UploadSequence,
    # Primitives — disabled by default, re-enable in config/tools.yaml for
    # power-user flows or when a stronger model is plugged in.
    "gene_to_locus": GeneToLocus,
    "predict_tracks": PredictTracks,
    "predict_variant_effect": PredictVariantEffect,
    "list_tracks_by_assay": ListTracksByAssay,
    "get_top_tracks": GetTopTracks,
    "render_panel": RenderPanel,
    "parse_hgvs": ParseHgvs,
}


def build_enabled_tools(session_context) -> list[Tool]:
    """Construct tool instances for every tool enabled in config/tools.yaml.

    `session_context` is passed to tools that need to read/write session state
    (uploads, last prediction). Tools that don't use it simply ignore the arg.
    """
    cfg = load_settings().tools
    out: list[Tool] = []
    for name, cls in _ALL.items():
        if cfg.get(name) and cfg[name].enabled:
            out.append(cls(session_context=session_context))
    return out
