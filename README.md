# OmniGemmaNome

Interactive functional-genomics exploration via chat. A Gemma-2B-IT agent (served by Ollama, driven by [smolagents](https://github.com/huggingface/smolagents)) orchestrates calls to an [alphagenome-pytorch](https://huggingface.co/gtca/alphagenome_pytorch) predictor and renders the results in an IGV.js-based web UI.

## Layout

```
config/              YAML configuration, loaded via pydantic-settings
services/
  alphagenome_svc/   FastAPI microservice wrapping the 450M-param AlphaGenome model
  agent_backend/     FastAPI + smolagents agent, exposes SSE chat endpoint
frontend/            Vite + React + TS, 1/3 chat left · 2/3 visualization right
scripts/             One-time setup: download genome, model weights, pull ollama model
docs/                Agent-facing tool reference
```

## Setup

Assets live outside git. One-time:

```bash
./scripts/pull_ollama_model.sh     # ollama pull gemma:2b
./scripts/download_model.sh        # AlphaGenome all-folds weights from HF Hub
./scripts/download_genome.sh       # hg38.fa + index
```

Python deps (both services share a workspace):

```bash
uv sync                             # or: pip install -e services/alphagenome_svc -e services/agent_backend
```

Frontend:

```bash
cd frontend && npm install
```

## Run (dev)

Four processes:

```bash
ollama serve                                                             # :11434
uv run uvicorn services.alphagenome_svc.app:app --port 8001              # :8001
uv run uvicorn services.agent_backend.app:app --port 8000                # :8000
cd frontend && npm run dev                                               # :5173
```

Or `docker-compose up` once that's wired.

## Health checks

```bash
curl localhost:8001/health
curl localhost:8000/health
```

## Status

Scaffold. AlphaGenome track metadata is a placeholder — see [services/alphagenome_svc/data/track_metadata.tsv](services/alphagenome_svc/data/track_metadata.tsv) and [scripts/fetch_track_metadata.py](scripts/fetch_track_metadata.py).
