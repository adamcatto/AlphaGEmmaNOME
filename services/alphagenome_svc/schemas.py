from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Head = Literal[
    "atac",
    "dnase",
    "procap",
    "cage",
    "rna_seq",
    "chip_tf",
    "chip_histone",
    "contact_maps",
    "splice_sites",
    "splice_junctions",
    "splice_site_usage",
]

Resolution = Literal["1bp", "128bp"]
Organism = Literal["human", "mouse"]


class PredictRequest(BaseModel):
    locus: str | None = None
    sequence: str | None = None
    organism: Organism = "human"
    heads: list[Head] = Field(default_factory=lambda: ["rna_seq", "chip_tf", "atac"])
    resolution: Resolution = "128bp"

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "PredictRequest":
        if (self.locus is None) == (self.sequence is None):
            raise ValueError("Provide exactly one of `locus` or `sequence`.")
        return self


class TrackArray(BaseModel):
    head: Head
    shape: list[int]
    dtype: str
    data_b64: str
    track_indices: list[int]


class PredictResponse(BaseModel):
    prediction_id: str
    locus: str | None
    organism: Organism
    resolution: Resolution
    arrays: list[TrackArray]


class TrackInfo(BaseModel):
    """A single AlphaGenome output track.

    Mirrors the columns of the DeepMind-published track_metadata TSV, with
    `head` derived from the TSV's `output_type` (uppercase → lowercase).
    """

    organism: Organism
    head: Head
    track_index: int
    name: str
    strand: str | None = None
    assay_title: str | None = None
    file_assembly: str | None = None
    data_source: str | None = None
    target_label: str | None = None
    ontology_curie: str | None = None
    biosample_name: str | None = None
    biosample_type: str | None = None
    gtex_tissue: str | None = None
    gtex_tissue_group: str | None = None
    experiment_accession: str | None = None
    file_accession: str | None = None
    frip: float | None = None
    nonzero_mean: float | None = None


class TracksResponse(BaseModel):
    tracks: list[TrackInfo]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model_loaded: bool
    genome_indexed: bool
    track_metadata_loaded: bool
    device: str


# ---------------------------------------------------------------------------
# Attribution (ISM-based)
# ---------------------------------------------------------------------------

class AttributionRequest(BaseModel):
    locus: str
    head: Head
    track_index: int
    attribution_region: str | None = None
    resolution: Resolution = "128bp"
    organism: Organism = "human"


class PerBaseScores(BaseModel):
    position: int
    ref_base: str
    A: float
    C: float
    G: float
    T: float


class AttributionResponse(BaseModel):
    locus: str
    attribution_region: str
    head: str
    track_index: int
    positions: list[int]
    ref_bases: list[str]
    importance_scores: list[float]
    per_base_scores: list[PerBaseScores]
    reference_signal: float
    summary: str


# ---------------------------------------------------------------------------
# Sequence optimization (ISM-ranked mutations)
# ---------------------------------------------------------------------------

class OptimizeSequenceRequest(BaseModel):
    locus: str
    head: Head
    track_index: int
    design_region: str | None = None
    top_k: int = 5
    organism: Organism = "human"


class OptimizeMutation(BaseModel):
    position: int
    ref: str
    alt: str
    delta_signal: float
    percent_change: float


class OptimizeSequenceResponse(BaseModel):
    locus: str
    design_region: str
    head: str
    track_index: int
    reference_signal: float
    top_mutations: list[OptimizeMutation]
    summary: str


# ---------------------------------------------------------------------------
# Genome edit optimization (SNVs, deletions, insertions, motifs)
# ---------------------------------------------------------------------------

class OptimizeEditsRequest(BaseModel):
    locus: str
    design_region: str | None = None
    edit_type: Literal["snv", "deletion", "insertion", "motif"]
    objective_head: Head
    objective_track: int
    objective_mode: Literal["maximize", "minimize", "target"]
    target_value: float | None = None
    max_edits: int = 1
    organism: Organism = "human"
    top_k: int = 5
    # Insertion / motif parameters if needed:
    motif_name: str | None = None  # e.g., "CTCF" for insertion or ablation


class EditedCandidate(BaseModel):
    mutation_type: str  # "snv", "deletion", "insertion", "motif"
    position: int       # 1-based genomic coordinate (start of edit)
    length: int         # length of edit
    sequence_change: str  # e.g. "C>A" or "del 10bp" or "ins CTCF_motif"
    predicted_signal: float
    percent_change: float


class OptimizeEditsResponse(BaseModel):
    locus: str
    design_region: str
    objective_head: Head
    objective_track: int
    reference_signal: float
    candidates: list[EditedCandidate]
    summary: str
    ref_seq: str | None = None
    edited_seq: str | None = None

