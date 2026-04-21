from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from schema import load_settings

import json

from .agent import build_agent
from .router import classify, stream_conversational
from .sessions import STORE
from .streaming import EventBus

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Agent backend starting. Ollama=%s", load_settings().ollama.base_url)
    yield


settings = load_settings()

app = FastAPI(title="OmniGemmaNome Agent Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.server.cors_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class UploadResponse(BaseModel):
    upload_id: str
    length: int
    session_id: str


class HealthResponse(BaseModel):
    status: str
    ollama_reachable: bool
    alphagenome_reachable: bool


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    ollama_ok = False
    ag_ok = False
    try:
        with httpx.Client(timeout=3.0) as c:
            r = c.get(f"{settings.ollama.base_url}/api/tags")
            ollama_ok = r.status_code == 200
    except Exception:
        ollama_ok = False
    try:
        with httpx.Client(timeout=3.0) as c:
            r = c.get(f"{settings.alphagenome.service_url}/health")
            ag_ok = r.status_code == 200
    except Exception:
        ag_ok = False
    return HealthResponse(
        status="ok" if ollama_ok and ag_ok else "degraded",
        ollama_reachable=ollama_ok,
        alphagenome_reachable=ag_ok,
    )


@app.post("/upload", response_model=UploadResponse)
async def upload(session_id: str | None = None, file: UploadFile = None) -> UploadResponse:
    if file is None:
        raise HTTPException(400, "No file.")
    raw = (await file.read()).decode("utf-8", errors="ignore")
    if len(raw.encode()) > settings.limits.max_upload_bytes:
        raise HTTPException(413, "Upload too large.")

    sequence = "".join(
        line.strip() for line in raw.splitlines() if line and not line.startswith(">")
    ).upper()
    if not sequence or any(c not in "ACGTN" for c in sequence):
        raise HTTPException(400, "File does not contain a valid DNA sequence.")

    session = STORE.get_or_create(session_id)
    upload_id = str(uuid.uuid4())
    session.uploads[upload_id] = {"sequence": sequence, "source": file.filename}
    return UploadResponse(upload_id=upload_id, length=len(sequence), session_id=session.id)


@app.get("/sessions/{session_id}/predictions/{prediction_id}/tracks")
def get_session_tracks(
    session_id: str,
    prediction_id: str,
    head: str = Query(...),
    indices: str | None = Query(None, description="Comma-separated track indices. All if omitted."),
    max_points: int = Query(2000, ge=32, le=8192),
):
    """Return per-position signal arrays for the given prediction + head, downsampled
    for rendering. Requires the prediction to still be in session memory."""
    session = STORE.get(session_id)
    if session is None:
        raise HTTPException(404, "Unknown session.")
    pred = session.last_prediction
    if pred is None or pred.get("prediction_id") != prediction_id:
        raise HTTPException(404, "Prediction not in session memory (may have been evicted).")
    arr = pred["arrays"].get(head)
    if arr is None:
        raise HTTPException(404, f"Head {head!r} not in prediction.")

    # AlphaGenome outputs arrive as (batch, positions, tracks); strip a singleton
    # batch dim so this endpoint always sees (positions, tracks) for 1D heads.
    if arr.ndim == 3 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim != 2:
        raise HTTPException(
            400,
            f"Head {head!r} has shape {arr.shape} — not a 1D signal track; use the contact-map panel instead.",
        )

    track_count = arr.shape[1]
    if indices:
        try:
            idx_list = [int(x) for x in indices.split(",") if x.strip()]
        except ValueError as e:
            raise HTTPException(400, f"Bad indices: {e}") from e
        for i in idx_list:
            if i < 0 or i >= track_count:
                raise HTTPException(400, f"Index {i} out of range [0, {track_count}).")
    else:
        idx_list = list(range(min(track_count, 16)))

    positions = arr.shape[0]
    # Block-mean downsample so peaks stay visible without shipping 130k points/track.
    if positions > max_points:
        bucket = positions // max_points
        truncated = arr[: bucket * max_points, idx_list]
        reshaped = truncated.reshape(max_points, bucket, len(idx_list))
        values = reshaped.mean(axis=1)
    else:
        values = arr[:, idx_list]

    # Annotate with track metadata (assay/cell_type/biosample) from alphagenome_svc.
    # Best-effort: failure here should not break the render.
    metadata_by_idx: dict[int, dict] = {}
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(
                f"{settings.alphagenome.service_url}/tracks",
                params={"head": head, "organism": pred.get("organism", "human")},
            )
            if r.status_code == 200:
                for t in r.json().get("tracks", []):
                    metadata_by_idx[int(t["track_index"])] = t
    except Exception:
        logger.warning("metadata fetch failed for head=%s", head, exc_info=True)

    return {
        "prediction_id": prediction_id,
        "head": head,
        "locus": pred.get("locus"),
        "resolution": pred.get("resolution"),
        "positions": positions,
        "downsampled_to": values.shape[0],
        "tracks": [
            {
                "track_index": int(ti),
                "values": [round(float(v), 4) for v in values[:, col]],
                "max": float(np.max(values[:, col])),
                "mean": float(np.mean(values[:, col])),
                "metadata": metadata_by_idx.get(int(ti)),
            }
            for col, ti in enumerate(idx_list)
        ],
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    session = STORE.get_or_create(req.session_id)
    bus = EventBus()
    bus.bind(asyncio.get_running_loop())

    def run() -> None:
        session.bus = bus
        try:
            route = classify(req.message)
            logger.info("router classified %r -> %s", req.message, route)
            if route == "ANSWER":
                answer = stream_conversational(
                    req.message, session.history, bus, session.id
                )
                session.history.append({"role": "user", "content": req.message})
                session.history.append({"role": "assistant", "content": answer})
                return

            agent = build_agent(session, bus)
            result = agent.run(req.message)
            answer = str(result) if result is not None else ""
            session.history.append({"role": "user", "content": req.message})
            session.history.append({"role": "assistant", "content": answer})
            # step_callback already emits `final` when is_final_answer fires;
            # this is a safety net for agents that return without that flag.
            bus.emit("final", {"session_id": session.id, "answer": answer})
        except Exception as e:
            logger.exception("agent run failed")
            bus.emit("error", {"session_id": session.id, "error": str(e)})
        finally:
            session.bus = None

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, run)

    async def event_stream():
        async for frame in bus.iterator():
            # sse_starlette accepts dicts with 'event' + 'data' keys. We stringify
            # the payload here because EventSourceResponse passes data through as-is.
            yield {"event": frame["event"], "data": json.dumps(frame["data"], default=str)}

    return EventSourceResponse(event_stream())
