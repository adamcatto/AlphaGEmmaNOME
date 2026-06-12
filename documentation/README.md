# AlphaGEmmaNOME Documentation

Interactive functional-genomics exploration via chat. A small local LLM (Qwen 3 through Ollama) drives a [smolagents](https://github.com/huggingface/smolagents) loop whose primary tool is an [alphagenome-pytorch](https://huggingface.co/gtca/alphagenome_pytorch) predictor. Results render in a custom React + canvas track viewer on the right half of the screen.

```
┌────────────────────┐    SSE    ┌─────────────────────┐   HTTP   ┌──────────────────────┐
│  React (Vite)      │◀─────────▶│  Agent backend      │◀────────▶│ AlphaGenome svc      │
│  ChatPane | VizPane│   POST    │  FastAPI +          │   :8001  │ FastAPI + pytorch    │
│  :5173             │──────────▶│  smolagents + Ollama│          │ hg38.fa via pyfaidx  │
└────────────────────┘           │  :8000              │          └──────────────────────┘
                                 └──────────┬──────────┘
                                            │ HTTP :11434
                                            ▼
                                   ┌─────────────────┐
                                   │ Ollama          │
                                   │ qwen3:4b        │
                                   └─────────────────┘
```

## Getting started

- [getting-started.md](getting-started.md) — install, download assets, run all four processes, first chat turn.
- [operations.md](operations.md) — docker-compose, health checks, troubleshooting, scaling notes.

## Reference

- [architecture.md](architecture.md) — processes, request lifecycles, threading model, why each boundary exists.
- [configuration.md](configuration.md) — every YAML key, the Pydantic schema, env-variable overrides.
- [api-reference.md](api-reference.md) — HTTP endpoints on agent-backend and alphagenome-svc with full request/response shapes.
- [sse-protocol.md](sse-protocol.md) — the streaming event types the frontend consumes.
- [agent-tools.md](agent-tools.md) — macros, primitives, planned stubs; input/output schemas; routing.
- [frontend.md](frontend.md) — component map, state store, SSE client, track renderer internals.
- [data-model.md](data-model.md) — Pydantic models and TypeScript types, including the track-metadata schema.

## Tutorials

- [tutorials/01-gene-tf-binding.md](tutorials/01-gene-tf-binding.md) — ask where TFs bind near a gene.
- [tutorials/02-region-regulation.md](tutorials/02-region-regulation.md) — profile a genomic region.
- [tutorials/03-variant-effect.md](tutorials/03-variant-effect.md) — compare ref vs alt predictions for an SNV.
- [tutorials/04-upload-sequence.md](tutorials/04-upload-sequence.md) — run AlphaGenome on a user-supplied sequence.
- [tutorials/05-adding-a-tool.md](tutorials/05-adding-a-tool.md) — implement and register a new agent tool.
- [tutorials/06-direct-api-usage.md](tutorials/06-direct-api-usage.md) — use the HTTP APIs without the agent.

## Repository layout

| Path | Role |
|---|---|
| [config/](../config/) | YAML configuration, pydantic-settings schema, shared across services |
| [services/alphagenome_svc/](../services/alphagenome_svc/) | FastAPI microservice wrapping alphagenome-pytorch |
| [services/agent_backend/](../services/agent_backend/) | FastAPI + smolagents + Ollama + tool registry |
| [frontend/](../frontend/) | Vite + React + TS; custom track viewer + IGV.js fallback |
| [scripts/](../scripts/) | One-time asset setup: genome FASTA, model weights, Ollama model |
| [docs/](../docs/) | Agent-facing tool reference (legacy — superseded by [agent-tools.md](agent-tools.md)) |

## Status

Scaffold. Known gaps:

- AlphaGenome track metadata is a placeholder until [scripts/fetch_track_metadata.py](../scripts/fetch_track_metadata.py) is filled in with DeepMind's canonical table.
- [ContactMapPlot](../frontend/src/components/ContactMapPlot.tsx) and splice viewers ship with synthetic data; only `igv_tracks` is real end-to-end.
- No session persistence — sessions live in-process (see [sessions.py](../services/agent_backend/sessions.py)); restarting the backend drops in-flight predictions.
