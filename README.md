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

---

## Inverse Genome Editing & Agent Alignment Loop

We have extended OmniGemmaNome with a powerful inverse-design genomic editing engine and a full closed-loop human-feedback + automated alignment (RLAIF) pipeline.

### 🧬 Inverse Genome Editing Engine
An advanced unified solver supports multiple edit styles to achieve target functional objectives (maximizing, minimizing, or matching specific values on regulatory tracks):
* **Beam Search (Multi-site SNVs):** Greedily searches combinations of up to $N$ synergistic SNV substitutions evaluated by In-Silico Mutagenesis (ISM) to find optimal multi-site variants.
* **Sliding-Window Deletion Scanner:** Evaluates sliding deletions of 5, 10, 25, or 50 bp, fetching trailing adjacent genomic sequences to maintain fixed locus sequence sizes for perfect coordinate alignment.
* **Motif-Aware Insertion / Ablation Solver:** Inserts consensus binding motifs (CTCF, SP1, AP-1, TATA, OCT4, NF-kB) systematically across design region spacing, or searches for and ablates existing motifs (with up to 2 mismatches) using scrambled substitution or deletion.

Registered on the AlphaGenome microservice as `/optimize_edits` and exposed to the LangGraph agent as the `optimize_edits` tool.

### 📊 Web UI Comparison & Alignment Portal
The React web interface is enhanced with deep user feedback controls:
1. **HTML5 Canvas Overlaid Comparison:** Renders reference (solid color) vs. edited state (bright coral dashed line) on the same plot for immediate, high-fidelity comparative profiles. Includes dual hover-tooltips.
2. **Inline Feedback & Step Corrections:** Thumbs-up/down button logs preferences. Users can edit agent reasoning steps (thoughts) and tool call parameters directly inline before executing them to log golden-standard Supervised Fine-Tuning (SFT) training data.
3. **Alternative A/B Regeneration:** Disliking a response allows A/B testing alternative reasoning paths via a dynamic history-popping endpoint (popping assistant/user turns).
4. **Dedicated Alignment Dashboard:** A persistent sidebar tab visualizes logged data stats (DPO preference counts, SFT correction counts), displays split inspectors of logged trajectories, and provides instant dataset exports to JSONL.

### 🤖 Closed-Loop Auto-RL Trajectory Generator
Instead of waiting for humans, the automated `scripts/auto_rl_generator.py` script generates genomic editing challenges, interacts with the agent via local SSE HTTP connections, and evaluates proposed edits using the AlphaGenome simulator as an Oracle. Successful plans are committed to SFT training sets; unsuccessful attempts form DPO pairs.

### 🎓 PEFT/QLoRA Local Model Alignment
Training templates using Hugging Face's `trl` library let you train local Gemma models on-premise:
* `scripts/train_sft.py`: Supervised Fine-Tuning (QLoRA) on expert corrections.
* `scripts/train_dpo.py`: Direct Preference Optimization (DPO) on pairwise preference choices.
* `scripts/merge_and_deploy.sh`: Automatic pipeline script that merges adapters with the base `google/gemma-4-E4B-it` weights, generates a custom Ollama Modelfile, and registers it as `gemma-4-omnigenomanome` locally.

### 🧪 Exhaustive Pytest Suite
Run the backend test suite verifying endpoints, schemas, solvers scoring, and tool specifications:
```bash
PYTHONPATH=. uv run --with pytest pytest -v tests/test_backend.py
```

