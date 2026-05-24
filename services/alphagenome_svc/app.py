from __future__ import annotations

import base64
import csv
import logging
import time
import uuid
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from schema import load_settings

from .model_loader import get_model
from .schemas import (
    AttributionRequest,
    AttributionResponse,
    Head,
    HealthResponse,
    Organism,
    OptimizeSequenceRequest,
    OptimizeSequenceResponse,
    OptimizeEditsRequest,
    OptimizeEditsResponse,
    PerBaseScores,
    PredictRequest,
    PredictResponse,
    TrackArray,
    TrackInfo,
    TracksResponse,
)
from .sequence import fetch_sequence

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_model()
    yield


settings = load_settings()

app = FastAPI(title="AlphaGenome Service", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.server.cors_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


_OUTPUT_TYPE_TO_HEAD = {
    "ATAC": "atac",
    "DNASE": "dnase",
    "PROCAP": "procap",
    "CAGE": "cage",
    "RNA_SEQ": "rna_seq",
    "CHIP_TF": "chip_tf",
    "CHIP_HISTONE": "chip_histone",
    "CONTACT_MAPS": "contact_maps",
    "SPLICE_SITES": "splice_sites",
    "SPLICE_JUNCTIONS": "splice_junctions",
    "SPLICE_SITE_USAGE": "splice_site_usage",
}


def _opt(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    return v


def _opt_float(v: str | None) -> float | None:
    v = _opt(v)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _load_track_metadata() -> list[TrackInfo]:
    path = settings.paths.track_metadata_tsv
    if not path.exists():
        return []
    out: list[TrackInfo] = []
    with path.open() as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            head = _OUTPUT_TYPE_TO_HEAD.get((row.get("output_type") or "").strip())
            if head is None:
                continue
            try:
                out.append(
                    TrackInfo(
                        organism=row["organism"],
                        head=head,
                        track_index=int(row["track_index"]),
                        name=row.get("name") or "",
                        strand=_opt(row.get("strand")),
                        assay_title=_opt(row.get("Assay title")),
                        file_assembly=_opt(row.get("File assembly")),
                        data_source=_opt(row.get("data_source")),
                        target_label=_opt(row.get("Target label")),
                        ontology_curie=_opt(row.get("ontology_curie")),
                        biosample_name=_opt(row.get("biosample_name")),
                        biosample_type=_opt(row.get("biosample_type")),
                        gtex_tissue=_opt(row.get("gtex_tissue")),
                        gtex_tissue_group=_opt(row.get("gtex_tissue_group")),
                        experiment_accession=_opt(row.get("Experiment accession")),
                        file_accession=_opt(row.get("File accession")),
                        frip=_opt_float(row.get("frip")),
                        nonzero_mean=_opt_float(row.get("nonzero_mean")),
                    )
                )
            except Exception as e:
                logger.warning("Skipping track row (idx=%r): %s", row.get("track_index"), e)
    return out


_tracks_cache: list[TrackInfo] | None = None


def get_tracks() -> list[TrackInfo]:
    global _tracks_cache
    if _tracks_cache is None:
        _tracks_cache = _load_track_metadata()
    return _tracks_cache


_BASE_ORDER = {"A": 0, "C": 1, "G": 2, "T": 3}

def _one_hot(sequence: str):
    import torch
    n = len(sequence)
    t = torch.zeros(n, 4, dtype=torch.float32)
    for i, base in enumerate(sequence.upper()):
        idx = _BASE_ORDER.get(base)
        if idx is not None:
            t[i, idx] = 1.0
    return t.unsqueeze(0)


def _encode_array(arr: np.ndarray) -> TrackArray:
    arr = np.ascontiguousarray(arr.astype(np.float16))
    return TrackArray(
        head="rna_seq",  # overwritten by caller
        shape=list(arr.shape),
        dtype=str(arr.dtype),
        data_b64=base64.b64encode(arr.tobytes()).decode("ascii"),
        track_indices=list(range(arr.shape[-1])) if arr.ndim >= 1 else [],
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    model = get_model()
    tracks = get_tracks()
    return HealthResponse(
        status="ok" if model is not None else "degraded",
        model_loaded=model is not None,
        genome_indexed=settings.paths.genome_fai.exists(),
        track_metadata_loaded=len(tracks) > 0,
        device=settings.alphagenome.device,
    )


@app.get("/sequence")
def sequence(locus: str) -> dict[str, str]:
    try:
        seq = fetch_sequence(locus, settings.paths.genome_fasta)
    except FileNotFoundError:
        raise HTTPException(503, "Reference genome not available.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"locus": locus, "sequence": seq}


@app.get("/tracks", response_model=TracksResponse)
def tracks(
    head: Head | None = Query(default=None),
    assay: str | None = Query(default=None, description="Substring matched against assay_title and target_label."),
    tissue: str | None = Query(default=None, description="Substring matched against biosample_name, gtex_tissue, gtex_tissue_group."),
    organism: Organism | None = Query(default=None),
) -> TracksResponse:
    rows = get_tracks()
    if head:
        rows = [r for r in rows if r.head == head]
    if organism:
        rows = [r for r in rows if r.organism == organism]
    if assay:
        needle = assay.lower()
        rows = [
            r for r in rows
            if (r.assay_title and needle in r.assay_title.lower())
            or (r.target_label and needle in r.target_label.lower())
        ]
    if tissue:
        needle = tissue.lower()
        rows = [
            r for r in rows
            if (r.biosample_name and needle in r.biosample_name.lower())
            or (r.gtex_tissue and needle in r.gtex_tissue.lower())
            or (r.gtex_tissue_group and needle in r.gtex_tissue_group.lower())
        ]
    return TracksResponse(tracks=rows)


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    model = get_model()
    if model is None:
        raise HTTPException(503, "AlphaGenome weights not loaded.")

    if req.locus is not None:
        req.locus = _adjust_locus_to_multiple(req.locus)

    if req.sequence is not None:
        sequence = req.sequence.upper()
    else:
        try:
            sequence = fetch_sequence(req.locus, settings.paths.genome_fasta)
        except FileNotFoundError:
            raise HTTPException(503, "Reference genome not available.")
        except ValueError as e:
            raise HTTPException(400, str(e))

    if len(sequence) > settings.limits.max_sequence_length:
        raise HTTPException(
            400,
            f"Sequence length {len(sequence)} exceeds max {settings.limits.max_sequence_length}.",
        )

    import torch

    dna = _one_hot(sequence).to(settings.alphagenome.device)
    organism_idx = settings.models.organism_index[req.organism]
    res_int = {"1bp": 1, "128bp": 128}[req.resolution]

    logger.info(
        "AlphaGenome forward pass starting: locus=%s seq_len=%d heads=%s resolution=%s device=%s organism=%s",
        req.locus,
        len(sequence),
        list(req.heads),
        req.resolution,
        settings.alphagenome.device,
        req.organism,
    )
    t0 = time.time()
    with torch.no_grad():
        preds = model.predict(
            dna,
            organism_index=organism_idx,
            heads=tuple(req.heads),
            resolutions=(res_int,),
        )
    elapsed = time.time() - t0
    logger.info(
        "AlphaGenome forward pass complete: locus=%s heads=%s in %.2fs",
        req.locus,
        list(req.heads),
        elapsed,
    )

    arrays: list[TrackArray] = []
    for head in req.heads:
        if head not in preds:
            raise HTTPException(400, f"Head {head!r} not in model outputs.")
        head_out = preds[head]
        if isinstance(head_out, dict):
            if res_int in head_out:
                tensor = head_out[res_int]
            elif len(head_out) == 1:
                tensor = next(iter(head_out.values()))
            else:
                raise HTTPException(
                    400,
                    f"Head {head!r} does not expose resolution {req.resolution}; keys={list(head_out)}",
                )
        else:
            tensor = head_out
        arr = tensor.detach().cpu().numpy()
        track_array = _encode_array(arr)
        track_array.head = head
        if arr.ndim >= 1:
            track_array.track_indices = list(range(arr.shape[-1]))
        arrays.append(track_array)

    return PredictResponse(
        prediction_id=str(uuid.uuid4()),
        locus=req.locus,
        organism=req.organism,
        resolution=req.resolution,
        arrays=arrays,
    )


# ---------------------------------------------------------------------------
# ISM helpers shared by /attribution and /optimize_sequence
# ---------------------------------------------------------------------------

_ISM_BASES = "ACGT"
_ISM_MAX_REGION_BP = 256  # hard cap on ISM window to prevent runaway runtimes


def _parse_locus_coords(locus: str) -> tuple[str, int, int]:
    chrom, coords = locus.split(":", 1)
    start_s, end_s = coords.split("-", 1)
    return chrom, int(start_s), int(end_s)


def _adjust_locus_to_multiple(locus: str, multiple: int = 2048) -> str:
    """Symmetrically expand a locus to be a multiple of `multiple` (e.g. 2048 bp)
    and at least 16,384 base-pairs (2**14).
    
    This ensures compatibility with deep UNet convolutional downsampling in the model,
    while satisfying the minimum sequence input length requirement.
    """
    try:
        chrom, start, end = _parse_locus_coords(locus)
    except Exception:
        return locus
    length = end - start
    if length <= 0:
        return locus

    # Ensure length is at least 16,384 bp (2**14)
    min_len = 16384
    if length < min_len:
        needed = min_len - length
        left_pad = needed // 2
        right_pad = needed - left_pad
        start = max(1, start - left_pad)
        end = end + right_pad
        # If we hit start=1, compensate on the right end to maintain exact min_len
        actual_len = end - start
        if actual_len < min_len:
            end += (min_len - actual_len)
        length = end - start

    # Symmetrically expand to next multiple of 2048 if needed
    if length % multiple != 0:
        needed = multiple - (length % multiple)
        left_pad = needed // 2
        right_pad = needed - left_pad
        new_start = max(1, start - left_pad)
        new_end = end + right_pad
        actual_len = new_end - new_start
        if actual_len % multiple != 0:
            new_end += (multiple - (actual_len % multiple))
        return f"{chrom}:{new_start}-{new_end}"

    return f"{chrom}:{start}-{end}"


def _resolve_subregion(locus: str, sub: str | None, half_default: int) -> tuple[str, int, int]:
    """Return (chrom, start, end) for the ISM sub-region."""
    locus_chrom, locus_start, locus_end = _parse_locus_coords(locus)
    if sub:
        chrom, start, end = _parse_locus_coords(sub)
    else:
        center = (locus_start + locus_end) // 2
        start = max(locus_start, center - half_default)
        end = min(locus_end, center + half_default)
        chrom = locus_chrom
    # Clamp to locus boundaries and enforce max cap
    start = max(start, locus_start)
    end = min(end, locus_end)
    end = min(end, start + _ISM_MAX_REGION_BP)
    return chrom, start, end


@app.post("/attribution", response_model=AttributionResponse)
def attribution(req: AttributionRequest) -> AttributionResponse:
    model = get_model()
    if model is None:
        raise HTTPException(503, "AlphaGenome weights not loaded.")

    req.locus = _adjust_locus_to_multiple(req.locus)

    try:
        ref_seq = fetch_sequence(req.locus, settings.paths.genome_fasta)
    except FileNotFoundError:
        raise HTTPException(503, "Reference genome not available.")
    except ValueError as e:
        raise HTTPException(400, str(e))

    chrom, attr_start, attr_end = _resolve_subregion(
        req.locus, req.attribution_region, half_default=32
    )
    locus_chrom, locus_start, _ = _parse_locus_coords(req.locus)
    attr_region_str = f"{chrom}:{attr_start}-{attr_end}"

    organism_idx = settings.models.organism_index[req.organism]
    res_int = {"1bp": 1, "128bp": 128}[req.resolution]

    # Reference signal
    ref_preds = model.predict_sequence(ref_seq, organism_idx, (req.head,), (res_int,))
    try:
        ref_signal = model.extract_track_signal(ref_preds, req.head, req.track_index, res_int)
    except (KeyError, IndexError) as e:
        raise HTTPException(400, str(e))

    positions: list[int] = []
    ref_bases: list[str] = []
    importance_scores: list[float] = []
    per_base_scores: list[PerBaseScores] = []

    logger.info(
        "ISM attribution: locus=%s region=%s head=%s track=%d n_positions=%d",
        req.locus, attr_region_str, req.head, req.track_index, attr_end - attr_start,
    )

    for genomic_pos in range(attr_start, attr_end):
        seq_offset = genomic_pos - locus_start
        if seq_offset < 0 or seq_offset >= len(ref_seq):
            continue

        ref_base = ref_seq[seq_offset].upper()
        alts = [b for b in _ISM_BASES if b != ref_base]

        scores: dict[str, float] = {"A": 0.0, "C": 0.0, "G": 0.0, "T": 0.0}
        scores[ref_base] = 0.0  # delta of ref vs ref is 0
        max_abs_delta = 0.0

        for alt in alts:
            mut_seq = ref_seq[:seq_offset] + alt + ref_seq[seq_offset + 1 :]
            alt_preds = model.predict_sequence(mut_seq, organism_idx, (req.head,), (res_int,))
            alt_signal = model.extract_track_signal(alt_preds, req.head, req.track_index, res_int)
            delta = alt_signal - ref_signal
            scores[alt] = round(float(delta), 6)
            max_abs_delta = max(max_abs_delta, abs(delta))

        positions.append(genomic_pos)
        ref_bases.append(ref_base)
        importance_scores.append(round(max_abs_delta, 6))
        per_base_scores.append(
            PerBaseScores(
                position=genomic_pos,
                ref_base=ref_base,
                A=scores["A"],
                C=scores["C"],
                G=scores["G"],
                T=scores["T"],
            )
        )

    top_pos = positions[importance_scores.index(max(importance_scores))] if importance_scores else None
    summary = (
        f"ISM attribution over {len(positions)} positions in {attr_region_str}. "
        f"Most important position: {top_pos} (score={max(importance_scores):.4f})."
        if importance_scores
        else f"No positions analyzed in {attr_region_str}."
    )

    return AttributionResponse(
        locus=req.locus,
        attribution_region=attr_region_str,
        head=req.head,
        track_index=req.track_index,
        positions=positions,
        ref_bases=ref_bases,
        importance_scores=importance_scores,
        per_base_scores=per_base_scores,
        reference_signal=round(ref_signal, 6),
        summary=summary,
    )


@app.post("/optimize_sequence", response_model=OptimizeSequenceResponse)
def optimize_sequence(req: OptimizeSequenceRequest) -> OptimizeSequenceResponse:
    model = get_model()
    if model is None:
        raise HTTPException(503, "AlphaGenome weights not loaded.")

    req.locus = _adjust_locus_to_multiple(req.locus)

    try:
        ref_seq = fetch_sequence(req.locus, settings.paths.genome_fasta)
    except FileNotFoundError:
        raise HTTPException(503, "Reference genome not available.")
    except ValueError as e:
        raise HTTPException(400, str(e))

    chrom, des_start, des_end = _resolve_subregion(
        req.locus, req.design_region, half_default=50
    )
    locus_chrom, locus_start, _ = _parse_locus_coords(req.locus)
    design_region_str = f"{chrom}:{des_start}-{des_end}"

    organism_idx = settings.models.organism_index[req.organism]
    # Use 128bp resolution for speed
    res_int = 128

    ref_preds = model.predict_sequence(ref_seq, organism_idx, (req.head,), (res_int,))
    try:
        ref_signal = model.extract_track_signal(ref_preds, req.head, req.track_index, res_int)
    except (KeyError, IndexError) as e:
        raise HTTPException(400, str(e))

    logger.info(
        "Sequence optimization: locus=%s region=%s head=%s track=%d n_pos=%d",
        req.locus, design_region_str, req.head, req.track_index, des_end - des_start,
    )

    mutations: list[dict] = []
    for genomic_pos in range(des_start, des_end):
        seq_offset = genomic_pos - locus_start
        if seq_offset < 0 or seq_offset >= len(ref_seq):
            continue

        ref_base = ref_seq[seq_offset].upper()
        for alt in _ISM_BASES:
            if alt == ref_base:
                continue
            mut_seq = ref_seq[:seq_offset] + alt + ref_seq[seq_offset + 1 :]
            alt_preds = model.predict_sequence(mut_seq, organism_idx, (req.head,), (res_int,))
            alt_signal = model.extract_track_signal(alt_preds, req.head, req.track_index, res_int)
            delta = alt_signal - ref_signal
            pct = (delta / max(abs(ref_signal), 1e-9)) * 100.0
            mutations.append(
                {
                    "position": genomic_pos,
                    "ref": ref_base,
                    "alt": alt,
                    "delta_signal": round(float(delta), 6),
                    "percent_change": round(float(pct), 2),
                }
            )

    # Sort by delta descending; only keep top_k with positive delta
    mutations.sort(key=lambda m: -m["delta_signal"])
    top = mutations[: req.top_k]

    best = top[0] if top else None
    summary = (
        f"Top mutation: {best['ref']}>{best['alt']}@{best['position']} "
        f"(Δ={best['delta_signal']:+.4f}, {best['percent_change']:+.1f}%)."
        if best
        else f"No beneficial mutations found in {design_region_str}."
    )

    return OptimizeSequenceResponse(
        locus=req.locus,
        design_region=design_region_str,
        head=req.head,
        track_index=req.track_index,
        reference_signal=round(ref_signal, 6),
        top_mutations=[
            OptimizeMutation(
                position=m["position"],
                ref=m["ref"],
                alt=m["alt"],
                delta_signal=m["delta_signal"],
                percent_change=m["percent_change"],
            )
            for m in top
        ],
        summary=summary,
    )


@app.post("/optimize_edits", response_model=OptimizeEditsResponse)
def optimize_edits(req: OptimizeEditsRequest) -> OptimizeEditsResponse:
    from .solver import solve_optimize_edits

    req.locus = _adjust_locus_to_multiple(req.locus)

    chrom, des_start, des_end = _resolve_subregion(
        req.locus, req.design_region, half_default=50
    )
    design_region_str = f"{chrom}:{des_start}-{des_end}"

    try:
        result = solve_optimize_edits(
            locus=req.locus,
            design_region_str=design_region_str,
            edit_type=req.edit_type,
            objective_head=req.objective_head,
            objective_track=req.objective_track,
            objective_mode=req.objective_mode,
            target_value=req.target_value,
            max_edits=req.max_edits,
            organism=req.organism,
            top_k=req.top_k,
            motif_name=req.motif_name,
        )
    except Exception as e:
        raise HTTPException(400, str(e))

    return OptimizeEditsResponse(**result)

