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
    Head,
    HealthResponse,
    Organism,
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


def _one_hot(sequence: str):
    from alphagenome_pytorch.utils.sequence import sequence_to_onehot_tensor

    return sequence_to_onehot_tensor(sequence).unsqueeze(0)


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
