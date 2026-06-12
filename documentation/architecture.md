# Architecture

## Process topology (dev)

Four long-running processes:

| Process | Port | Purpose |
|---|---|---|
| `ollama serve` | 11434 | Local LLM host. Serves the model named in [config/ollama.yaml](../config/ollama.yaml) (default `qwen3:4b`). |
| `uvicorn services.alphagenome_svc.app:app` | 8001 | Loads AlphaGenome weights once; exposes `/predict`, `/sequence`, `/tracks`, `/health`. |
| `uvicorn services.agent_backend.app:app` | 8000 | Runs the smolagents loop; exposes `/chat` (SSE), `/upload`, `/sessions/{id}/predictions/{pid}/tracks`, `/health`. |
| `vite dev` (frontend) | 5173 | React UI; connects to agent-backend over POST SSE. |

All four can also run under [docker-compose.yml](../docker-compose.yml); see [operations.md](operations.md).

## Why three back-end services?

- **Ollama** isolates the LLM. Swapping models (`qwen3:4b` → `qwen3:8b` → `gemma:2b`) is a config change, not a code change, because [ollama_model.py](../services/agent_backend/ollama_model.py) goes through LiteLLM's `ollama_chat/` provider.
- **alphagenome-svc** isolates the heavy-weight model. The 450M-parameter AlphaGenome loads once (`get_model()` in [model_loader.py](../services/alphagenome_svc/model_loader.py)) and holds a module-level singleton. Moving it to a GPU host is a URL change in [config/alphagenome.yaml](../config/alphagenome.yaml).
- **agent-backend** owns sessions, streaming, and the tool registry. It never touches torch — it just calls `POST /predict` on alphagenome-svc and decodes the base64 float16 arrays that come back.

## The `/chat` request lifecycle

1. Frontend POSTs `{message, session_id?}` to `/chat`. The response is an SSE stream ([api/chat.ts](../frontend/src/api/chat.ts)).
2. [app.py](../services/agent_backend/app.py) creates or fetches a `Session`, wires a fresh `EventBus` to the running asyncio loop, and dispatches the agent run to a thread via `loop.run_in_executor`. Running the agent in a worker thread keeps the LLM/tool latency from blocking the SSE generator.
3. The **router** ([router.py](../services/agent_backend/router.py)) first classifies the message as `PREDICT` or `ANSWER` with a one-word LLM prompt. On `ANSWER` it streams a conversational reply directly (no tool loop). On `PREDICT` it builds the agent.
4. [agent.py](../services/agent_backend/agent.py) instantiates `smolagents.ToolCallingAgent` with:
   - The `TolerantLiteLLMModel` wrapper around LiteLLM ([ollama_model.py](../services/agent_backend/ollama_model.py)).
   - The tools enabled in [config/tools.yaml](../config/tools.yaml) ([tools/__init__.py](../services/agent_backend/tools/__init__.py)).
   - A `step_callback` that emits `thought`, `tool_call_start`, `tool_call_result`, `viz_spec`, and `final` events onto the `EventBus`.
   - A system prompt composed of [prompts/system.md](../services/agent_backend/prompts/system.md) + [prompts/few_shot.json](../services/agent_backend/prompts/few_shot.json) + smolagents' own system prompt template.
5. Each agent tool runs in the worker thread. If it's a macro ([tools/macros.py](../services/agent_backend/tools/macros.py)), it calls alphagenome-svc directly via `httpx` and stashes the raw tensors on `session.last_prediction` for later rendering.
6. When a tool returns a `viz_spec`, the macro stashes it on `session.pending_viz_spec`. The next step-callback emission surfaces it as a `viz_spec` event so the frontend can open a new tab.
7. `final_answer` fires → the callback emits `final` → the SSE generator's iterator sees `final` and closes the stream.

## The render path (after `viz_spec` arrives)

1. Frontend's [ChatPane](../frontend/src/components/ChatPane.tsx) receives the `viz_spec` SSE frame and calls `useStore.pushViz(spec)`. This appends to the `vizSpecs` array in [state/store.ts](../frontend/src/state/store.ts) and sets `activeVizIndex` to the new tab.
2. [VizPane](../frontend/src/components/VizPane.tsx) renders the active spec. For `igv_tracks`, it mounts [TrackViewer](../frontend/src/components/TrackViewer.tsx).
3. [TrackViewer](../frontend/src/components/TrackViewer.tsx) calls `GET /sessions/{sid}/predictions/{pid}/tracks?head=…&indices=…` ([api/tracks.ts](../frontend/src/api/tracks.ts)).
4. The agent-backend endpoint (in [app.py](../services/agent_backend/app.py)) pulls the in-memory numpy array out of `session.last_prediction`, block-mean downsamples from ~131,072 positions to `max_points` (default 2,000), annotates each track with metadata fetched from alphagenome-svc `GET /tracks`, and returns the rows.
5. TrackViewer draws each row as a filled area + stroked outline on a `<canvas>`, colored by head (`chip_tf` purple, `atac` orange, etc.). Hover shows per-position values.

## Threading model

- **asyncio event loop** (uvicorn) owns HTTP request/response, the SSE generator, and the upload endpoint.
- **worker thread** (one per in-flight agent run) runs `agent.run()`. It makes blocking calls to LiteLLM/Ollama and to alphagenome-svc.
- **EventBus** bridges the two: the worker calls `bus.emit(event, payload)`, which dispatches `asyncio.run_coroutine_threadsafe` onto the bound loop. The SSE generator reads from the same queue.

This keeps a single process capable of serving multiple concurrent chats at the cost of one agent thread each. The alphagenome-svc model is process-global and is NOT thread-safe for parallel forward passes — set `batch_size: 1` (the default) and rely on Python's GIL plus FastAPI's single-threaded endpoint dispatch to serialize inference.

## Session state

In-memory only. Defined in [sessions.py](../services/agent_backend/sessions.py):

```python
@dataclass
class Session:
    id: str
    created_at: float
    last_used_at: float
    history: list[dict]          # chat history for the conversational router
    uploads: dict[str, dict]     # upload_id → {sequence, source}
    last_prediction: dict | None # {prediction_id, locus, arrays, resolution, organism}
    pending_viz_spec: dict | None
    bus: Any                      # EventBus attached for the current /chat call
```

A per-session `predict_cache` dict is also attached lazily inside `_predict` ([macros.py](../services/agent_backend/tools/macros.py)) keyed on `(locus, sequence, heads, resolution, organism)`. This exists because Qwen3 occasionally re-issues an identical tool call after a parse failure, and AlphaGenome forward passes on CPU cost 8–15 seconds per call.

Sessions have a TTL (`session_ttl_seconds`, default 3600s) enforced by `SessionStore.gc()` — but nothing calls it automatically. Background GC is a TODO.

## The tool-call tolerance layer

Small local LLMs emit several variants of "tool call": OpenAI-style JSON, Qwen's `<tool_call>{...}</tool_call>` blocks, bare JSON blobs, and `<think>...</think>` reasoning leaks. [TolerantLiteLLMModel](../services/agent_backend/ollama_model.py) normalizes all of these so smolagents sees a clean `ChatMessage`:

1. `<think>...</think>` content is stripped from content and moved to `message.content` as the reasoning text. This surfaces in the UI as a collapsible "thinking" block via the `thought` SSE event.
2. `<tool_call>...</tool_call>` blocks are parsed into `ChatMessageToolCall` objects. If that tag is missing, a raw JSON object starting with `{` is treated as a tool call too.
3. One level of key-alias tolerance: `{"name": "X", "arguments": {...}}` and `{"function": {"name": "X", "parameters": {...}}}` both work; nested wrappers are unwrapped.

This is why the agent works reliably with a 4B-parameter model even though the model's raw output is frequently malformed.

## Why macros instead of primitives

The tools registry ([tools/__init__.py](../services/agent_backend/tools/__init__.py)) exposes two tiers:

- **Macros** run an entire pipeline in one tool call: `analyze_gene_tf_binding`, `analyze_region_regulation`, `analyze_variant_effect`. Default-enabled.
- **Primitives** are the individual steps: `gene_to_locus`, `predict_tracks`, `get_top_tracks`, `render_panel`, etc. Default-disabled.

Rationale in [config/tools.yaml](../config/tools.yaml): small LLMs cannot plan reliable multi-step chains. Enabling primitives triggers cascades like `gene_to_locus` → `predict_tracks` (wrong window) → `list_tracks_by_assay` loop. A stronger model (Qwen 2.5 14B, Llama 3.1 8B+) can chain primitives; toggle in `config/tools.yaml`.

## Configuration boundary

Both Python services import `from schema import load_settings`. The [config/](../config/) package is a separate uv workspace member. YAML files are layered: `settings.yaml` → `settings.{dev,prod}.yaml` → environment variables with the `ALPHAGEMMA_` prefix and `__` as nested-delimiter (e.g. `ALPHAGEMMA_ALPHAGENOME__DEVICE=cuda`). See [configuration.md](configuration.md).
