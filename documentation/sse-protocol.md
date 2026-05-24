# SSE Streaming Protocol

`POST /chat` opens a Server-Sent Events stream. The frontend consumes these with a fetch+ReadableStream reader (not `EventSource`, because the agent-backend requires a POST). See [api/chat.ts](../frontend/src/api/chat.ts).

## Wire format

```
event: <event_type>
data: <json-encoded payload>

```

Two blank lines (either `\n\n` or `\r\n\r\n`) terminate a frame. The agent-backend uses `sse-starlette`, which defaults to CRLF; the client regex is `/\r?\n\r?\n/` to tolerate both.

## Event types

Declared in [streaming.py](../services/agent_backend/streaming.py) and [frontend/src/types/viz_spec.ts](../frontend/src/types/viz_spec.ts).

### `token`

Streaming output from the conversational branch (router classified the message as `ANSWER`). Not used by the tool-calling agent path.

```json
{"text": "Hello, yes APOE is …"}
```

Frontend appends to `message.content` character by character.

### `thought`

Model reasoning leaking out of a `<think>...</think>` block or LiteLLM's `reasoning_content` field. Surfaced as a collapsible "thinking" UI element. Emitted during both routes:

- Conversational: directly from `reasoning_content` chunks.
- Agent: from `step.model_output` after each agent step ([agent.py:50-51](../services/agent_backend/agent.py#L50-L51)).

```json
{"text": "The user asked about BRCA1. I should look up its coordinates first…"}
```

Multiple thought frames are concatenated with double-newlines.

### `progress`

Structured progress indicator for long-running operations. Currently emitted by the AlphaGenome call path ([macros.py](../services/agent_backend/tools/macros.py)):

```json
{"stage": "alphagenome_start", "text": "Running AlphaGenome on chr17:… for heads=['chip_tf']…"}
{"stage": "alphagenome_done", "text": "AlphaGenome forward pass completed in 12.3s"}
{"stage": "alphagenome_cached", "text": "Reusing previous AlphaGenome result (same args)."}
{"stage": "alphagenome_error", "text": "AlphaGenome failed (503) after 0.2s"}
```

The `stage` field drives the UI dot color:

- Ends in `_done` → green
- Ends in `_error` → red
- Otherwise → amber (in-progress)

### `tool_call_start`

Agent kicked off a tool invocation.

```json
{
  "tool": "analyze_gene_tf_binding",
  "arguments": {"gene_symbol": "BRCA1", "tissue_keywords": ["brain"]}
}
```

Frontend appends a new `ToolCall` entry with `status: "pending"`. `arguments` may be a dict, a JSON-encoded string, or `null` depending on how the LLM formatted its call.

### `tool_call_result`

Tool returned (successfully or not — the exception would have become an error observation).

```json
{"observation": "{'gene': {...}, 'top_tracks': [...]}"}
```

The observation is the string-coerced return value of the tool, truncated to 4000 characters. Frontend updates the latest `ToolCall` to `status: "done"` with `observation` attached.

### `viz_spec`

A macro produced a visualization to render. The payload is a `VizSpec` dict:

```json
{
  "type": "igv_tracks",
  "prediction_id": "uuid",
  "locus": "chr17:43044295-43175367",
  "head": "chip_tf",
  "track_indices": [1204, 881, 331, ...]
}
```

Supported `type`s (from [viz_spec.ts](../frontend/src/types/viz_spec.ts)):

| `type` | Rendered by | Status |
|---|---|---|
| `igv_tracks` | [TrackViewer](../frontend/src/components/TrackViewer.tsx) (or [IgvTrackBrowser](../frontend/src/components/IgvTrackBrowser.tsx) behind a toggle) | fully wired |
| `contact_map` | [ContactMapPlot](../frontend/src/components/ContactMapPlot.tsx) | **placeholder** data — not wired to real tensors |
| `splice_arcs` | JSON fallback | **not implemented** |
| `variant_delta` | JSON fallback | **not implemented** |

Emission: when a macro returns `{"viz_spec": {...}}`, [`_stash_viz`](../services/agent_backend/tools/macros.py) copies it to `session.pending_viz_spec`; the next step-callback pass in [agent.py](../services/agent_backend/agent.py) reads that field and fires the SSE event.

### `error`

Something threw. The stream closes after this frame.

```json
{"session_id": "uuid", "error": "RuntimeError: alphagenome_svc /predict 503: ..."}
```

Frontend replaces the in-progress assistant message content with `Error: <text>`.

### `final`

Turn complete. Last frame of the stream.

```json
{"session_id": "uuid", "answer": "AlphaGenome predicts …"}
```

Frontend:

1. Persists `session_id` into the zustand store (so the next turn reuses the session).
2. If the assistant bubble already has content from `token` events, leaves it; otherwise fills in `answer`.
3. Clears the `streaming: true` flag.

## Guarantees and ordering

- Exactly one `final` OR one `error` per stream.
- `viz_spec` may appear 0 or more times before `final`. One per macro call.
- `tool_call_start` and `tool_call_result` are paired in order per tool, but multiple tool calls within a turn are possible if the LLM ignores the "one tool per question" rule.
- `thought` frames can interleave freely with anything else — they represent the model's state, not a structured event.
- `progress` frames are emitted synchronously from tool code, so they arrive between the `tool_call_start` and `tool_call_result` of their owning tool.

## Closing the stream

The SSE generator in [app.py:227-232](../services/agent_backend/app.py#L227-L232) iterates the `EventBus`, and [`EventBus.iterator`](../services/agent_backend/streaming.py) breaks out of the loop on the first `final` or `error` event. The HTTP response then naturally ends.

If the client disconnects mid-turn, the worker thread keeps running until the agent loop completes or hits `max_steps` — there's no cancellation signal. This is fine for current workloads (one CPU-bound forward pass, 10s-ish) but would need a cancel token for a GPU-hosted deployment with parallel chats.

## Testing with curl

```bash
curl -N -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is CTCF?"}'
```

You'll see a stream of `token` events followed by a `final`. The `-N` flag (`--no-buffer`) is important — without it, curl buffers output and you'll see nothing until the stream closes.
