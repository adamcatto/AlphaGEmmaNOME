# Tutorial 6 — Hit the APIs directly (no agent, no frontend)

For scripting, benchmarking, or batch work, you can bypass the chat loop entirely. Talk to `alphagenome-svc` for predictions and metadata, and to `agent-backend` for the agent, uploads, and track rendering.

See [api-reference.md](../api-reference.md) for the full endpoint surface; this tutorial shows concrete invocations for the common cases.

## Setup

Start just the two services you need:

```bash
# terminal 1
uv run uvicorn services.alphagenome_svc.app:app --port 8001

# terminal 2 (only if you want /upload or /chat)
uv run uvicorn services.agent_backend.app:app --port 8000
```

Skip Ollama and the frontend for pure inference work.

## Run a prediction by locus

[alphagenome-svc](../../services/alphagenome_svc/app.py) exposes `POST /predict`. Give it a locus and the heads you want:

```bash
curl -s -X POST localhost:8001/predict \
  -H "Content-Type: application/json" \
  -d '{
        "locus": "chr17:43044295-43175367",
        "heads": ["chip_tf", "atac"],
        "resolution": "128bp",
        "organism": "human"
      }' | jq '.arrays[] | {head, shape, dtype}'
```

Response shape ([PredictResponse](../data-model.md#predictresponse)):

```json
{
  "prediction_id": "…uuid…",
  "locus": "chr17:43044295-43175367",
  "organism": "human",
  "resolution": "128bp",
  "arrays": [
    {
      "head": "chip_tf",
      "shape": [1, 1024, 1664],
      "dtype": "float16",
      "data_b64": "…",
      "track_indices": [0, 1, …, 1663]
    },
    { "head": "atac", "shape": [1, 1024, 256], "dtype": "float16", "data_b64": "…", "track_indices": […] }
  ]
}
```

Decode in Python:

```python
import base64, numpy as np, requests

r = requests.post("http://localhost:8001/predict", json={
    "locus": "chr17:43044295-43175367",
    "heads": ["chip_tf"],
    "resolution": "128bp",
})
r.raise_for_status()
pred = r.json()

arr_obj = pred["arrays"][0]
buf = base64.b64decode(arr_obj["data_b64"])
arr = np.frombuffer(buf, dtype=arr_obj["dtype"]).reshape(arr_obj["shape"])
# arr.shape == (1, 1024, 1664) — (batch, positions, tracks)
arr = arr[0]   # drop batch dim
top_tracks = np.argsort(-arr.mean(axis=0))[:5]
print("Top-5 chip_tf tracks by mean:", top_tracks.tolist())
```

The `1024` position dimension is `131072 / 128` (resolution 128bp). For `resolution: "1bp"` it's `131072`.

## Predict on a user-supplied sequence

Swap `locus` for `sequence`:

```python
seq = "A" * 131072   # must be exactly 131,072 bp
r = requests.post("http://localhost:8001/predict", json={
    "sequence": seq,
    "heads": ["rna_seq"],
    "resolution": "128bp",
})
```

Exactly one of `locus` or `sequence` is required — the schema validator rejects both-or-neither with `422`. Sequence must be ACGTN-only and exactly `max_sequence_length` bp (see [config/settings.yaml](../../config/settings.yaml), default 131072).

## Fetch the reference sequence

Useful for variant-effect work — you want the exact bases around a coordinate:

```bash
curl -s "localhost:8001/sequence?locus=chr17:43044200-43044300" | jq -r .sequence
# GCCTGACAGAG…
```

This is 1-based inclusive per HGVS convention. The slice is served from [data/hg38.fa](../../services/alphagenome_svc/data/hg38.fa) via pyfaidx — the file must exist on the alphagenome-svc host (see [scripts/download_genome.sh](../../scripts/download_genome.sh)).

## Query track metadata

```bash
curl -s "localhost:8001/tracks?head=chip_tf&organism=human&target_label=CTCF" | jq '.tracks | length'
# e.g. 47
```

Filters (all optional, substring-matched case-insensitively):

| Param | Column(s) searched |
|---|---|
| `head` | `head` (exact) |
| `organism` | `organism` (exact) |
| `assay` | `assay_title` + `target_label` |
| `tissue` | `biosample_name` + `gtex_tissue` + `gtex_tissue_group` |
| `target_label` | `target_label` |

Multiple filters are ANDed. Row limit comes from the TSV (1,664 for chip_tf; see [data-model.md](../data-model.md#track-metadata-tsv-schema) for the full count table).

## Run the agent programmatically

Skip the frontend but still use the LLM routing:

```python
import json, httpx

def chat(message, session_id=None):
    with httpx.stream("POST", "http://localhost:8000/chat",
                       json={"message": message, "session_id": session_id},
                       timeout=120.0) as r:
        session_id_out = None
        for line in r.iter_lines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
                if event == "final":
                    return data.get("content"), data.get("session_id", session_id_out)
                if event == "viz_spec":
                    print("got viz_spec:", data)

answer, session_id = chat("Where do TFs bind near BRCA1?")
print(answer)
```

Event types are documented in [sse-protocol.md](../sse-protocol.md). For scripted work you probably only care about `final`.

## Call a macro directly (no agent)

Bypass the LLM entirely — import the tool class and call `forward`:

```python
from services.agent_backend.tools.macros import AnalyzeGeneTfBinding

class FakeSession:
    pending_viz_spec = None
    last_prediction = None
    predict_cache = None
    bus = None   # disables progress event emission

tool = AnalyzeGeneTfBinding(session_context=FakeSession())
result = tool.forward(
    gene_symbol="ASPM",
    tissue_keywords=["brain"],
    top_k=10,
    organism="human",
)

for t in result["top_tracks"]:
    print(t["track_index"], t.get("target_label"), t["mean_signal"])
```

Still requires alphagenome-svc to be running — the macro POSTs to `/predict` internally. This is the fastest way to iterate on a new macro without restarting the agent-backend.

## Reconstruct a per-track signal array

The `/sessions/.../predictions/.../tracks` endpoint ([app.py:104](../../services/agent_backend/app.py#L104)) downsamples for UI rendering. For full-resolution analysis, decode the array client-side from `/predict` directly (as shown above) — the session endpoint is purpose-built for the frontend canvas renderer.

If you must go through the session endpoint:

```bash
curl -s "localhost:8000/sessions/$SID/predictions/$PID/tracks?head=chip_tf&indices=112,884&max_points=2000" | jq
```

Returns a `TracksResponse` with each track's values downsampled to `max_points` and rounded to 4 decimals. The `positions` field tells you the original array length; `downsampled_to` is what you got.

## Batch example: top-K TF tracks across a gene set

Put it all together — score 10 genes in a loop:

```python
import requests
from services.agent_backend.tools.macros import AnalyzeGeneTfBinding

class FakeSession:
    pending_viz_spec = None; last_prediction = None; predict_cache = None; bus = None

genes = ["BRCA1", "TP53", "ASPM", "MYC", "SOX2", "FOXP2", "PAX6", "NANOG", "KLF4", "ASCL1"]
tool = AnalyzeGeneTfBinding(session_context=FakeSession())

for g in genes:
    r = tool.forward(gene_symbol=g, tissue_keywords=None, top_k=3, organism="human")
    top = [(t.get("target_label"), round(t["mean_signal"], 2)) for t in r["top_tracks"]]
    print(f"{g:10s} {r['window']}  {top}")
```

On CPU, each call is ~15 s so ~2.5 minutes total. The per-session cache doesn't help here (one macro instance per gene), but if you reuse the same `FakeSession` across calls to the same gene, the second call hits `predict_cache`.

## Latency reference

| Operation | Cold (first call) | Warm |
|---|---|---|
| `/predict` (131 kb, one head, CPU) | +20 s weight load | 8–15 s |
| `/predict` (131 kb, one head, CUDA) | +20 s weight load | 0.3–1 s |
| `/predict` (same args, cached in-session) | — | <50 ms (cache hit) |
| `/sequence` (100 bp slice) | — | <20 ms |
| `/tracks` | — | <100 ms |
| `/upload` (131 kb FASTA) | — | <50 ms |
| `/chat` full agent turn | cold model load 20 s + 8–15 s forward pass | 12–20 s |

See [operations.md](../operations.md) for GPU deployment and scaling notes.

## Rate limits, auth, CORS

None of these are implemented. The services are intended to run behind a reverse proxy in production — put auth, rate limits, and explicit CORS there. The agent-backend does honor `cors_origins` in [config/settings.yaml](../../config/settings.yaml); set it before exposing.
