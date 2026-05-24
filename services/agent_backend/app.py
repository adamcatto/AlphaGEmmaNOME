from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from schema import load_settings

from .graph import get_graph
from .sessions import STORE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Agent backend starting. Ollama=%s", load_settings().ollama.base_url)
    # Warm the graph singleton so the first request doesn't pay compilation cost.
    get_graph()
    yield


settings = load_settings()

app = FastAPI(title="OmniGemmaNome Agent Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.server.cors_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

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


class PreferenceRequest(BaseModel):
    session_id: str
    prompt: str
    chosen: str
    rejected: str


class CorrectionRequest(BaseModel):
    session_id: str
    prompt: str
    user_message: str
    original: str
    corrected: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": json.dumps(data, default=str)}


def _is_reasoning_token(content: str) -> bool:
    """True while streaming inside a <think> block."""
    # We track inline think-block state in the stream handler instead of here;
    # this is just a helper for the closing-tag sentinel.
    return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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
    pred = getattr(session, "predictions", {}).get(prediction_id) or session.last_prediction
    if pred is None or pred.get("prediction_id") != prediction_id:
        raise HTTPException(404, "Prediction not in session memory (may have been evicted).")
    arr = pred["arrays"].get(head)
    if arr is None:
        raise HTTPException(404, f"Head {head!r} not in prediction.")

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
    if positions > max_points:
        bucket = positions // max_points
        truncated = arr[: bucket * max_points, idx_list]
        reshaped = truncated.reshape(max_points, bucket, len(idx_list))
        values = reshaped.mean(axis=1)
    else:
        values = arr[:, idx_list]

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


# ---------------------------------------------------------------------------
# /chat — LangGraph-powered SSE endpoint
# ---------------------------------------------------------------------------

_PREDICT_NODES = {"agent", "conversational"}


@app.post("/chat")
async def chat(req: ChatRequest):
    session = STORE.get_or_create(req.session_id)
    s = load_settings()

    # Inject history as prior messages so the agent has conversation context.
    history_msgs: list = []
    for turn in session.history[-(s.agent.memory_turns * 2):]:
        if turn["role"] == "user":
            history_msgs.append(HumanMessage(content=turn["content"]))
        else:
            history_msgs.append(AIMessage(content=turn["content"]))
    history_msgs.append(HumanMessage(content=req.message))

    initial_state = {
        "messages": history_msgs,
        "session_id": session.id,
        "intent": None,
        "viz_specs": [],
        "step_count": 0,
    }
    config = {
        "configurable": {"session_id": session.id},
        "recursion_limit": max(s.agent.max_steps * 3, 20),
    }

    graph = get_graph()

    async def event_stream():
        # State machine for stripping inline <think> blocks from token stream.
        in_think = False
        pending_token = ""
        final_emitted = False

        try:
            async for event in graph.astream_events(initial_state, config=config, version="v2"):
                etype = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")

                # --- token streaming ---
                if etype == "on_chat_model_stream" and node in _PREDICT_NODES:
                    chunk = event["data"]["chunk"]

                    # reasoning_content (Qwen3 / models with separate thinking field)
                    reasoning = (getattr(chunk, "additional_kwargs", {}) or {}).get("reasoning_content", "")
                    if reasoning:
                        yield _sse("thought", {"text": reasoning})

                    delta = chunk.content or ""
                    if not delta:
                        continue

                    pending_token += delta
                    # Strip <think>...</think> blocks, emit reasoning as thought events.
                    while pending_token:
                        if in_think:
                            end = pending_token.find("</think>")
                            if end < 0:
                                pending_token = ""
                                break
                            pending_token = pending_token[end + len("</think>"):]
                            in_think = False
                        else:
                            start = pending_token.find("<think>")
                            if start < 0:
                                yield _sse("token", {"text": pending_token})
                                pending_token = ""
                                break
                            if start > 0:
                                yield _sse("token", {"text": pending_token[:start]})
                            pending_token = pending_token[start + len("<think>"):]
                            in_think = True

                # --- tool lifecycle ---
                elif etype == "on_tool_start":
                    yield _sse("tool_call_start", {
                        "tool": event["name"],
                        "input": event["data"].get("input"),
                    })

                elif etype == "on_tool_end":
                    output = event["data"].get("output") or {}
                    yield _sse("tool_call_result", {
                        "tool": event["name"],
                        "output": str(output)[:4000],
                    })
                    # Surface viz_spec immediately rather than waiting for synthesizer.
                    if isinstance(output, dict) and output.get("viz_spec"):
                        yield _sse("viz_spec", output["viz_spec"])

                # --- graph completion ---
                elif etype == "on_chain_end" and event.get("name") == "LangGraph" and not final_emitted:
                    output_state = event["data"].get("output") or {}
                    msgs = output_state.get("messages") or []

                    # Find the last AI message (final answer).
                    answer = ""
                    for msg in reversed(msgs):
                        if getattr(msg, "type", None) == "ai" and msg.content:
                            answer = msg.content
                            break

                    # Emit any viz_specs surfaced by synthesizer_node.
                    for vs in output_state.get("viz_specs") or []:
                        yield _sse("viz_spec", vs)

                    session.history.append({"role": "user", "content": req.message})
                    session.history.append({"role": "assistant", "content": answer})
                    yield _sse("final", {"session_id": session.id, "answer": answer})
                    final_emitted = True

        except Exception as e:
            logger.exception("graph run failed for session %s", session.id)
            yield _sse("error", {"session_id": session.id, "error": str(e)})
            if not final_emitted:
                yield _sse("final", {"session_id": session.id, "answer": ""})

    return EventSourceResponse(event_stream())


# ---------------------------------------------------------------------------
# Feedback & Alignment Endpoints
# ---------------------------------------------------------------------------

@app.post("/feedback/preference")
def post_preference(req: PreferenceRequest):
    from .feedback_store import log_preference
    return log_preference(req.session_id, req.prompt, req.chosen, req.rejected)


@app.post("/feedback/correction")
def post_correction(req: CorrectionRequest):
    from .feedback_store import log_correction
    return log_correction(req.session_id, req.prompt, req.user_message, req.original, req.corrected)


@app.get("/feedback/stats")
def get_feedback_stats():
    from .feedback_store import get_stats
    return get_stats()


@app.get("/feedback/export")
def get_feedback_export(type: str = "dpo"):
    from .feedback_store import export_dataset
    if type not in ("dpo", "sft"):
        raise HTTPException(400, "Invalid type. Must be 'dpo' or 'sft'.")
    return export_dataset(type)


@app.post("/sessions/{session_id}/history/pop")
def pop_session_history(session_id: str):
    session = STORE.get(session_id)
    if session is None:
        raise HTTPException(404, "Unknown session.")
    if len(session.history) >= 2:
        session.history.pop()  # Pop assistant response
        session.history.pop()  # Pop user message
    elif len(session.history) == 1:
        session.history.pop()
    return {"status": "ok", "history_length": len(session.history)}


