# Operations

Running, deploying, monitoring, and troubleshooting.

## One-command dev via docker-compose

[docker-compose.yml](../docker-compose.yml) defines four services: `ollama`, `alphagenome-svc`, `agent-backend`, `frontend`.

```bash
docker-compose up
```

Service wiring:

- `agent-backend` gets `OMNIGEMMA_OLLAMA__BASE_URL=http://ollama:11434` and `OMNIGEMMA_ALPHAGENOME__SERVICE_URL=http://alphagenome-svc:8001` so it talks to the sibling containers by name.
- `frontend` is built as its own image; the browser still talks to `localhost:8000`, so `VITE_AGENT_BACKEND_URL` is set accordingly.
- `ollama_data` volume persists pulled models between restarts.
- `config/` is mounted read-only into both Python services; `services/alphagenome_svc/data/` is mounted read-write into the alphagenome-svc container (the model and genome live here).

Missing pieces in the scaffold you'll need to add for first run:

- `services/alphagenome_svc/Dockerfile` — install uv, copy workspace, `CMD uvicorn services.alphagenome_svc.app:app --host 0.0.0.0 --port 8001`.
- `services/agent_backend/Dockerfile` — same pattern, port 8000.
- `frontend/Dockerfile` — `npm install`, `npm run dev -- --host`.

Once those exist, `docker-compose up --build` gets you the full stack. `docker-compose exec ollama ollama pull qwen3:4b` to prime the model.

## Manual run (four terminals)

```bash
# terminal 1
ollama serve

# terminal 2
uv run uvicorn services.alphagenome_svc.app:app --port 8001

# terminal 3
uv run uvicorn services.agent_backend.app:app --port 8000 --reload

# terminal 4
cd frontend && npm run dev
```

Add `--reload` to the agent-backend in dev; the alphagenome-svc should NOT reload (would re-load weights every time you save a file).

## Health checks

```bash
curl -s localhost:8001/health | jq
# {
#   "status": "ok",
#   "model_loaded": true,
#   "genome_indexed": true,
#   "track_metadata_loaded": true,
#   "device": "cpu"
# }

curl -s localhost:8000/health | jq
# {
#   "status": "ok",
#   "ollama_reachable": true,
#   "alphagenome_reachable": true
# }
```

`ollama_reachable` checks `GET /api/tags` on `ollama.base_url` with a 3-second timeout. `alphagenome_reachable` pings the svc's own `/health`.

Both are read-only. Safe to hit from a Kubernetes liveness/readiness probe.

## Logs

Uvicorn's default format. Add `--log-level debug` for noisier output. The alphagenome-svc logs the forward-pass duration per request:

```
INFO services.alphagenome_svc AlphaGenome forward pass complete: locus=chr17:... heads=['chip_tf'] in 12.34s
```

Agent-backend logs router classification and agent runs:

```
INFO services.agent_backend router classified 'Where do TFs bind near BRCA1?' -> PREDICT
```

## GPU deployment

The AlphaGenome forward pass is 10–30× faster on GPU. Two moves:

1. Host alphagenome-svc on a machine with a CUDA-capable GPU and set `OMNIGEMMA_ALPHAGENOME__DEVICE=cuda`.
2. Point the agent-backend at it via `OMNIGEMMA_ALPHAGENOME__SERVICE_URL=http://<gpu-host>:8001`.

Weight-loading honors whatever torch sees; ensure the container has `nvidia-container-toolkit` and `--gpus all` (or a PyTorch CUDA base image).

A `cuda` dtype tweak is declared in config but NOT currently threaded through to `from_pretrained` — it runs at the checkpoint's native dtype (float32). For a quick win on a memory-constrained GPU, add `.half()` in [model_loader.py](../services/alphagenome_svc/model_loader.py) after `.to(device)` and cast inputs likewise.

## Scaling chats

Single-process limits:

- One agent run = one worker thread = one AlphaGenome forward pass at a time (the pytorch model is not thread-safe for parallel inference at `batch_size: 1`).
- Concurrent chats are possible but will serialize at the `/predict` HTTP boundary.

For parallel inference:

- Run multiple alphagenome-svc replicas behind a round-robin proxy. Each holds its own weight copy (~1.5 GB per process).
- Leave agent-backend as one replica (sessions are in-memory; no Redis yet).

For durable sessions, replace `SessionStore` in [sessions.py](../services/agent_backend/sessions.py) with a Redis-backed implementation. The dataclass serializes cleanly with `dataclasses.asdict` except for `bus` (excluded) and `last_prediction["arrays"]` (numpy; needs separate storage — consider streaming to disk or S3 and keeping only the `prediction_id` in Redis).

## Downloading assets in a container

[scripts/download_model.sh](../scripts/download_model.sh), [scripts/download_genome.sh](../scripts/download_genome.sh), and [scripts/pull_ollama_model.sh](../scripts/pull_ollama_model.sh) all resolve paths through [config/paths.yaml](../config/paths.yaml). In a docker context:

```bash
docker-compose run --rm alphagenome-svc ./scripts/download_model.sh
docker-compose run --rm alphagenome-svc ./scripts/download_genome.sh
docker-compose exec ollama ollama pull qwen3:4b
```

The first two write into `services/alphagenome_svc/data/` which is bind-mounted in compose, so the files land on the host too.

## Troubleshooting

### "Head 'X' is not a 1D signal track"

Returned by `GET /sessions/.../tracks` for 2D heads like `contact_maps`. The tracks endpoint handles only `(positions, tracks)` arrays. Route contact maps to a different component (currently [ContactMapPlot](../frontend/src/components/ContactMapPlot.tsx), which is wired to placeholder data — a backend endpoint for 2D retrieval is TODO).

### Agent stuck in a "thought loop"

Qwen3 occasionally emits `<think>` reasoning as free-form content instead of wrapping a clean tool call. Two mitigations are already in place:

- The [TolerantLiteLLMModel](../services/agent_backend/ollama_model.py) parses `<tool_call>` tags AND bare `{`-prefixed JSON as tool calls.
- The per-session [`predict_cache`](../services/agent_backend/tools/macros.py) short-circuits duplicate AlphaGenome forward passes when the agent retries with identical args.

If it still loops beyond `agent.max_steps: 6`, the run terminates and the safety-net `final` event fires with the smolagents result string. Consider upgrading to `qwen3:8b` or `qwen2.5:14b-instruct`.

### AlphaGenome OOMs on CPU

A 131-kb input at float32 across all 11 heads is ~400 MB of activations. Options:

- Reduce `heads` at the call site.
- Use `resolution: "128bp"` (default) rather than `"1bp"` (saves ~128× on 1D heads).
- Switch to GPU via `OMNIGEMMA_ALPHAGENOME__DEVICE=cuda`.

### Weights not loading

`GET /health` returns `model_loaded: false` until the first `/predict` call (lazy). If a `/predict` errors with "AlphaGenome weights not loaded":

```bash
ls -la services/alphagenome_svc/data/model_all_folds.safetensors
# If missing:
./scripts/download_model.sh
```

### Reference genome mismatch on variant

`apply_snv_to_sequence` raises `ValueError` if the base at `variant_position` doesn't match `ref`. Usually this is a coordinate-frame mistake (1-based vs 0-based) — the agent's `analyze_variant_effect` expects 1-based HGVS coordinates (e.g. `chr17:g.43044295A>G`), and internally the FASTA is sliced as `sequence[pos - start - 1]` (1-based → 0-based within the locus). If you call `/predict_variant_effect` directly, double-check your `variant_position`.

### Frontend shows no tracks after a successful agent turn

1. Open DevTools network tab, look for `/sessions/.../tracks` — any 404 means the session was lost (e.g. backend restarted mid-turn).
2. 400 "not a 1D signal track" — macro emitted a head that isn't in the 1D set. Check the `spec.head` in the SSE log.
3. 200 with empty `tracks: []` — `track_indices` was empty, which usually means `top_tracks` was filtered to zero by tissue keywords with no hits. The macro should have appended a `note`.

### Missing track metadata annotations

If track rows show `track 1204` instead of `CTCF · GM12878`, the metadata TSV isn't loaded. Verify:

```bash
curl -s "localhost:8001/tracks?head=chip_tf" | jq '.tracks | length'
```

Should return 1664. If 0, the placeholder TSV is empty or the header row is malformed — see [scripts/fetch_track_metadata.py](../scripts/fetch_track_metadata.py).

### Session TTL

Sessions time out after `limits.session_ttl_seconds` (default 3600). `SessionStore.gc()` removes them — but no background task calls it. To reclaim memory, either:

- Restart the process periodically.
- Add an asyncio periodic task in `app.lifespan` to call `STORE.gc()` every 5 minutes.

## Security notes

- No authentication. The agent-backend accepts any `/chat` and `/upload` request. Lock it behind a reverse proxy with auth before exposing.
- CORS defaults to `["*"]` when `cors_origins` is empty (production overlay leaves it empty). Set explicit origins in `config/settings.prod.yaml` before shipping.
- Uploaded sequences live in process memory only — nothing is written to disk by default. `upload_dir` is declared in [paths.yaml](../config/paths.yaml) but isn't used by the current code path.
- The agent runs with filesystem access via the Python import graph — don't enable `CodeAgent` (via `agent.agent_type: code`) on untrusted input.
