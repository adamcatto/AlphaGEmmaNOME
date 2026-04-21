# HTTP API Reference

Two services expose HTTP APIs:

- **agent-backend** (`:8000`) — chat, uploads, per-session track retrieval, health.
- **alphagenome-svc** (`:8001`) — prediction, sequence fetch, track metadata, health.

---

## agent-backend — [services/agent_backend/app.py](../services/agent_backend/app.py)

### `GET /health`

Checks the two downstream dependencies.

**Response 200** ([HealthResponse](../services/agent_backend/app.py)):

```json
{
  "status": "ok",
  "ollama_reachable": true,
  "alphagenome_reachable": true
}
```

`status` is `"ok"` only if both downstream services respond on their `/api/tags` and `/health` endpoints within 3 seconds; otherwise `"degraded"`.

---

### `POST /chat`

Runs one turn of the agent loop. The response is an SSE (Server-Sent Events) stream.

**Request body**:

```json
{
  "message": "string",
  "session_id": "string | null"
}
```

- `session_id` — if null or unknown, a new session is created and the new ID is included in the `final` event.

**Response**: `text/event-stream`. Each event frame is:

```
event: <event_type>
data: <json payload>

```

See [sse-protocol.md](sse-protocol.md) for the full event taxonomy. The stream closes after an `error` or `final` event.

**Classification**: the first thing the backend does is ask the LLM to classify the message as `PREDICT` or `ANSWER` ([router.py](../services/agent_backend/router.py)). `ANSWER` skips the agent loop and streams a conversational reply as `token` events. `PREDICT` spins up a `ToolCallingAgent`.

**Implementation notes**:

- The agent runs on a worker thread via `loop.run_in_executor(None, run)`. SSE generation runs on the asyncio loop. Events cross thread boundaries through the `EventBus` (see [streaming.py](../services/agent_backend/streaming.py)).
- If the LLM returns without calling `final_answer`, a safety-net `final` event is emitted with `answer = str(result)` so the stream still closes.

**Example** (curl):

```bash
curl -N -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Where do TFs bind near BRCA1?"}'
```

---

### `POST /upload`

Registers a user-supplied DNA sequence with a session.

**Query params**:

- `session_id` — optional; a new session is created if omitted.

**Form body**: `multipart/form-data` with a single `file` field. Accepted extensions (frontend-side only): `.fasta`, `.fa`, `.txt`.

**Processing** ([app.py:84-101](../services/agent_backend/app.py)):

1. Decoded as UTF-8.
2. Total byte size checked against `limits.max_upload_bytes`.
3. `>`-prefixed header lines stripped; remaining lines joined and uppercased.
4. Must contain only `ACGTN`.

**Response 200** ([UploadResponse](../services/agent_backend/app.py)):

```json
{
  "upload_id": "uuid",
  "length": 123456,
  "session_id": "uuid"
}
```

**Errors**:

- `400` — missing file, non-DNA content, or empty sequence.
- `413` — upload exceeds `max_upload_bytes`.

The uploaded sequence is stored in-memory on `session.uploads[upload_id]` and survives until the session is GC'd.

---

### `GET /sessions/{session_id}/predictions/{prediction_id}/tracks`

Returns per-position track arrays for rendering. Called by [TrackViewer](../frontend/src/components/TrackViewer.tsx) after a `viz_spec` event.

**Path params**:

- `session_id` — the session that owns the prediction.
- `prediction_id` — the UUID emitted by alphagenome-svc on `/predict` and stashed on `session.last_prediction`.

**Query params**:

| Param | Type | Default | Meaning |
|---|---|---|---|
| `head` | string (required) | — | Which AlphaGenome head to return |
| `indices` | string (CSV) | top 16 tracks | Subset of track indices |
| `max_points` | int (32–8192) | 2000 | Target downsampled resolution |

**Response 200**:

```json
{
  "prediction_id": "uuid",
  "head": "chip_tf",
  "locus": "chr17:43044295-43175367",
  "resolution": "128bp",
  "positions": 131072,
  "downsampled_to": 2000,
  "tracks": [
    {
      "track_index": 123,
      "values": [0.12, 0.34, ...],
      "max": 1.23,
      "mean": 0.41,
      "metadata": {
        "track_index": 123,
        "head": "chip_tf",
        "assay_title": "ChIP-seq",
        "target_label": "CTCF",
        "biosample_name": "GM12878",
        ...
      }
    }
  ]
}
```

**Downsampling** — if `positions > max_points`, the endpoint block-means the array down to `max_points` rows. Otherwise it returns the raw shape. Values are rounded to 4 decimal places to keep payloads small.

**Metadata** — fetched best-effort from alphagenome-svc `GET /tracks?head=<head>&organism=<organism>`. If that call fails, `metadata` is `null` but track data still returns.

**Errors**:

- `400` — bad `indices` (non-integer or out of range), or `head` is non-1D (e.g. `contact_maps`).
- `404` — unknown session, prediction evicted from memory, or `head` not in the prediction.

**Shape handling**: AlphaGenome arrays are stored as `(batch=1, positions, tracks)`. This endpoint squeezes the batch axis before checking `ndim == 2`. 2D heads like `contact_maps` are rejected here and should render through a different component.

---

## alphagenome-svc — [services/alphagenome_svc/app.py](../services/alphagenome_svc/app.py)

### `GET /health`

**Response 200**:

```json
{
  "status": "ok",
  "model_loaded": false,
  "genome_indexed": true,
  "track_metadata_loaded": true,
  "device": "cpu"
}
```

`model_loaded` is `false` until the first `/predict` call (weights are lazy-loaded). `status` is `"ok"` whenever `get_model()` returns non-None — which happens after the first successful load.

---

### `GET /sequence`

Fetch a reference slice from the hg38 FASTA.

**Query**: `locus=chrN:start-end` (e.g. `chr17:43044295-43125483`). Coordinates are 0-based half-open, following pyfaidx.

**Response 200**:

```json
{"locus": "chr17:43044295-43125483", "sequence": "ACGTACGT..."}
```

**Errors**:

- `400` — malformed locus, unknown chromosome, end ≤ start.
- `503` — FASTA file not found (run `./scripts/download_genome.sh`).

Used internally by [`_variant_utils.apply_snv_to_sequence`](../services/agent_backend/_variant_utils.py) to build the ALT sequence.

---

### `GET /tracks`

Filter and list track metadata.

**Query params** (all optional):

- `head` — one of the 11 heads.
- `assay` — substring matched (case-insensitive) against `assay_title` and `target_label`.
- `tissue` — substring matched against `biosample_name`, `gtex_tissue`, `gtex_tissue_group`.
- `organism` — `"human"` or `"mouse"`.

**Response 200** ([TracksResponse](../services/alphagenome_svc/schemas.py)):

```json
{
  "tracks": [
    {
      "organism": "human",
      "head": "chip_tf",
      "track_index": 123,
      "name": "ENCSR...",
      "strand": null,
      "assay_title": "ChIP-seq",
      "file_assembly": "GRCh38",
      "data_source": "ENCODE",
      "target_label": "CTCF",
      "ontology_curie": "CL:0000558",
      "biosample_name": "GM12878",
      "biosample_type": "cell line",
      "gtex_tissue": null,
      "gtex_tissue_group": null,
      "experiment_accession": null,
      "file_accession": null,
      "frip": null,
      "nonzero_mean": null
    }
  ]
}
```

Reads from [track_metadata.tsv](../services/alphagenome_svc/data/track_metadata.tsv) with a lazy global cache. `output_type` in the TSV is mapped to `head` via an uppercase→lowercase table (`RNA_SEQ` → `rna_seq`, `CHIP_TF` → `chip_tf`, etc.).

If the TSV is missing or empty, the endpoint returns `{"tracks": []}` rather than erroring — macros then show a "metadata unavailable" note.

---

### `POST /predict`

Run one AlphaGenome forward pass.

**Request body** ([PredictRequest](../services/alphagenome_svc/schemas.py)):

```json
{
  "locus": "chr17:43044295-43175367",
  "sequence": null,
  "organism": "human",
  "heads": ["chip_tf", "atac"],
  "resolution": "128bp"
}
```

- Provide **exactly one** of `locus` or `sequence`. `locus` triggers an internal FASTA fetch; `sequence` is used as-is (uppercased).
- `heads` — default `["rna_seq", "chip_tf", "atac"]`.
- `resolution` — `"1bp"` or `"128bp"`.

**Response 200** ([PredictResponse](../services/alphagenome_svc/schemas.py)):

```json
{
  "prediction_id": "uuid",
  "locus": "chr17:43044295-43175367",
  "organism": "human",
  "resolution": "128bp",
  "arrays": [
    {
      "head": "chip_tf",
      "shape": [1, 1024, 1664],
      "dtype": "float16",
      "data_b64": "…base64…",
      "track_indices": [0, 1, 2, ..., 1663]
    }
  ]
}
```

**Array encoding**: each array is `np.ascontiguousarray(tensor.astype(np.float16))`, bytes, then base64. Decode client-side with:

```python
import base64, numpy as np
raw = base64.b64decode(arr["data_b64"])
a = np.frombuffer(raw, dtype=arr["dtype"]).reshape(arr["shape"])
```

**Errors**:

- `400` — sequence longer than `limits.max_sequence_length` (131,072), unknown head, or resolution mismatch.
- `503` — weights or genome FASTA missing.

**Latency (CPU, all 11 heads, 128bp, 131kb input)**: ~15–25 s cold (first call), ~8–15 s warm. Use `OMNIGEMMA_ALPHAGENOME__DEVICE=cuda` for <1s/call on a decent GPU.

---

## Invariants and shape notes

- **Locus windows are always symmetric around a center**. Gene macros center on the gene midpoint `(start+end)//2` (see [macros.py:300](../services/agent_backend/tools/macros.py#L300) — this is a user-specified invariant). Variant macros center on the variant position.
- **Array shape is `(batch, positions, tracks)`** for 1D heads, always with `batch=1` because `batch_size: 1` in config. Contact maps and splice-related heads are higher-rank.
- **`prediction_id` is a random UUID per `/predict`**. It's the cache key inside the agent-backend session and the client-side correlation ID for tracks retrieval.
- **Heads are enum-validated** via `Head = Literal[…]` in [schemas.py](../services/alphagenome_svc/schemas.py). Invalid heads fail at Pydantic validation before hitting the model.
