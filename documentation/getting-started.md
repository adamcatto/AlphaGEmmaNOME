# Getting Started

End-to-end setup from a fresh checkout to a working chat turn.

## Prerequisites

- **Python 3.11+** and [uv](https://docs.astral.sh/uv/) (recommended) or `pip`.
- **Node 18+** and `npm`.
- **Ollama** installed and the binary on `PATH`. See [ollama.com/download](https://ollama.com/download).
- Optional: **samtools** for faster FASTA indexing. Without it, `pyfaidx` will build the index in Python (slower, same result).
- Disk budget: hg38 FASTA ≈ 3 GB, AlphaGenome weights ≈ 1.5 GB, Ollama qwen3:4b ≈ 3 GB.

## 1. Install dependencies

Python:

```bash
uv sync
```

This installs the root package and its three workspace members (`config`, `services/alphagenome_svc`, `services/agent_backend`).

Frontend:

```bash
cd frontend && npm install
```

## 2. Download assets (one time)

Three assets live outside git:

```bash
./scripts/pull_ollama_model.sh     # reads model name from config/ollama.yaml
./scripts/download_model.sh        # AlphaGenome all-folds from HF Hub
./scripts/download_genome.sh       # hg38.fa from UCSC + .fai index
```

Every script resolves its destination through [config/paths.yaml](../config/paths.yaml) via [scripts/_paths.py](../scripts/_paths.py), so changing paths in one YAML file moves everything consistently.

Gated HF models need a token:

```bash
export HUGGINGFACE_HUB_TOKEN=hf_...
```

(The default `gtca/alphagenome_pytorch` is public.)

## 3. Track metadata

The shipped [track_metadata.tsv](../services/alphagenome_svc/data/track_metadata.tsv) is a placeholder. Without it, tissue filtering in macros falls back to "top tracks by signal across all tissues" and the UI shows track indices without assay/sample annotations. Completing [scripts/fetch_track_metadata.py](../scripts/fetch_track_metadata.py) against DeepMind's published annotation table fixes this — see the module docstring for the expected schema.

## 4. Run the four processes

Open four terminals (or use a tool like `foreman` / `tmuxinator` / `docker-compose up`):

```bash
# Terminal 1
ollama serve

# Terminal 2
uv run uvicorn services.alphagenome_svc.app:app --port 8001 --host 0.0.0.0

# Terminal 3
uv run uvicorn services.agent_backend.app:app --port 8000 --host 0.0.0.0 --reload

# Terminal 4
cd frontend && npm run dev
```

The agent-backend takes a few seconds to start because it lazily imports smolagents + litellm; the alphagenome-svc does NOT load weights at startup — it loads on the first `/predict` call (the first forward pass costs 20–40 s on CPU, subsequent calls 8–15 s).

## 5. Health checks

```bash
curl localhost:8001/health
# {"status":"ok","model_loaded":false,"genome_indexed":true,"track_metadata_loaded":true,"device":"cpu"}
# model_loaded flips to true after the first /predict call.

curl localhost:8000/health
# {"status":"ok","ollama_reachable":true,"alphagenome_reachable":true}
```

`status: "degraded"` on either side indicates a broken dependency — check the individual reachable flags.

## 6. Your first chat turn

Open [http://localhost:5173](http://localhost:5173).

Try:

> Where do transcription factors bind near BRCA1?

What happens under the hood:

1. Frontend POSTs to `/chat` and holds open an SSE stream.
2. Router classifies as `PREDICT` (gene + binding question).
3. Agent picks `analyze_gene_tf_binding(gene_symbol="BRCA1")`.
4. Macro resolves BRCA1 via Ensembl → `chr17:43044295-43125483`.
5. Computes gene midpoint (the window is centered on gene midpoint, not the TSS — see [macros.py:300-301](../services/agent_backend/tools/macros.py#L300-L301)), extends to ±65 kb.
6. POSTs `/predict` to alphagenome-svc with `heads=["chip_tf"]`.
7. Ranks top tracks by mean signal, annotates with metadata, emits `viz_spec`.
8. Agent calls `final_answer` with a 2–4 sentence summary.
9. Frontend renders the track panel on the right.

Expected visible progression:

- `thinking` block fills in (Qwen3 reasoning).
- `analyze_gene_tf_binding` chip turns amber (pending) → green (done).
- Progress row: `Running AlphaGenome on chr17:… for heads=['chip_tf']…` → `AlphaGenome forward pass completed in X.Xs`.
- Right pane opens a new tab labeled `igv_tracks · chip_tf` with 10 track rows.
- Final assistant message summarizes the top 2–3 tracks.

## 7. Upload a sequence

The "Choose file" button under the chat accepts `.fasta` / `.fa` / `.txt`. Files over `limits.max_upload_bytes` (10 MiB default) or containing non-ACGTN characters are rejected with HTTP 400.

Once uploaded, the chat records the `upload_id`. Ask:

> Run AlphaGenome on upload_id=<id>.

The agent will pick `upload_sequence` then the user must follow up with a `predict_tracks` request — but since primitives are disabled by default, upload-based prediction is currently limited to the `upload_sequence` macro surface which just returns a preview. Enabling `predict_tracks` in [config/tools.yaml](../config/tools.yaml) gets you full sequence prediction with a stronger model.

## Next steps

- [tutorials/](tutorials/) — worked examples of every macro.
- [agent-tools.md](agent-tools.md) — full tool reference.
- [configuration.md](configuration.md) — tune ports, models, devices.
- [operations.md](operations.md) — docker-compose, GPU deployment, troubleshooting.
