# OmniGemmaNome

Interactive functional-genomics exploration via chat. A Gemma-based agent (served by Ollama, structured using a [LangGraph](https://github.com/langchain-ai/langgraph) conditional state graph) orchestrates calls to an [alphagenome-pytorch](https://huggingface.co/gtca/alphagenome_pytorch) predictor and renders the results in a custom interactive track viewer.

## Layout

```
config/              YAML configuration, loaded via pydantic-settings
services/
  alphagenome_svc/   FastAPI microservice wrapping the 450M-param AlphaGenome model
  agent_backend/     FastAPI + LangGraph agent (adapting legacy smolagents tools), exposes SSE chat endpoint
frontend/            Vite + React + TS, 1/3 chat left · 2/3 visualization right
scripts/             One-time setup: download genome, model weights, pull ollama model
docs/                Agent-facing tool reference
```

## Setup

Assets live outside git. One-time:

```bash
./scripts/pull_ollama_model.sh     # ollama pull gemma4:e4b
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

## Chat Interface & Agent Orchestration

OmniGemmaNome uses a state-of-the-art asynchronous multi-node orchestration engine to bridge the gap between human instruction and raw deep learning simulators.

### 🗺️ System Information Flow Architecture
When you type a query in the chat interface, information travels across three main components in real-time. The diagram below illustrates how user sessions, LangGraph states, and microservices exchange instructions, tool actions, and predictions:

```
[ User Browser (React UI) ]
      │
      │ 1. POST /chat (User Message + Session ID)
      ▼
[ agent_backend (FastAPI REST App) ]
      │
      │ 2. Instantiate StateGraph(initial_state)
      ▼
 ┌────────────────────────────────────────────────────────┐
 │ LangGraph State Machine (Orchestrator)                 │
 │                                                        │
 │        [ START ]                                       │
 │            │                                           │
 │            ▼                                           │
 │     [ Intent Router ]                                  │
 │       /          \                                     │
 │  ("answer")   ("predict")                              │
 │     /              \                                   │
 │    ▼                ▼                                  │
 │ [Conversational] [Agent Node]                          │
 │                      │   ▲                             │
 │             (Tool    │   │ (Tool                       │
 │             Calls)   ▼   │ Messages)                   │
 │                  [ToolNode]                            │
 │                      │                                 │
 │                      ▼                                 │
 │              [Synthesizer]                             │
 │                      │                                 │
 │                      ▼                                 │
 │                   [ END ]                              │
 └──────────────────────╂─────────────────────────────────┘
                        ┃
                        ┃ 3. Execute Tool Actions / Predictions
                        ▼
           [ alphagenome_svc (FastAPI Server) ]
                        │
                        │ 4. forward(dna, organism_index)
                        ▼
              [ AlphaGenome Predictor ]
                        │
                        │ 5. Return Predicted Epigenomic Tracks
                        ▼
           [ alphagenome_svc (FastAPI Server) ]
                        ┃
                        ┃ 6. Return Evaluation Signal or NDJSON /stream
                        ▼
[ agent_backend (FastAPI REST App) ]
      │
      │ 7. Server-Sent Events (SSE) Stream (chunks, thoughts, tool_starts, viz_specs, final)
      ▼
[ User Browser (React UI) ]
```

### 🔁 Asynchronous Event-Stream Life Cycle (SSE Protocol)
To ensure the user interface stays highly responsive and fluid, the agent backend and frontend communicate via a stateful Server-Sent Events (SSE) connection. This prevents the UI from blocking during long-running genomic operations and allows reasoning processes to be streamed progressively.

Below is a sequence diagram detailing the SSE life cycle of a single `/chat` transaction, showing how token chunks, inner `<think>` blocks, tool starts, tool results, progress updates, and final answers are progressively emitted to the React client:

```
User (Browser)           agent_backend (LangGraph)           alphagenome_svc (Solver)
     │                                │                                │
     │─────── 1. POST /chat ─────────>│                                │
     │        (User Query)            │                                │
     │                                │                                │
     │<── 2. Connection Established ──│                                │
     │    (EventSource / SSE)         │                                │
     │                                │                                │
     │                                │── 3. Classify intent ─────────>│
     │                                │    (Ollama Local Model)        │
     │                                │                                │
     │<── 4. emit "thought" ──────────│                                │
     │    (Reasoning/Think blocks)    │                                │
     │                                │                                │
     │                                │── 5. Trigger Tool Call ───────>│
     │                                │    (e.g., optimize_edits)      │
     │                                │                                │
     │<── 6. emit "tool_call_start" ──│                                │
     │    (Parameters displayed)      │                                │
     │                                │                                │
     │                                │                                │─── 7. RunISM / Evaluate ──┐
     │                                │                                │    (Predictor Loop)       │
     │                                │                                │<──────────────────────────┘
     │                                │                                │
     │                                │<── 8. Stream Progression ──────│
     │                                │    (NDJSON progress SSE)       │
     │                                │                                │
     │<── 9. emit "progress" ─────────│                                │
     │    (UI dynamic progress bar)   │                                │
     │                                │                                │
     │                                │<── 10. Return Best Candidate ──│
     │                                │    (Visual track spec dict)    │
     │                                │                                │
     │<── 11. emit "viz_spec" ────────│                                │
     │    (Draw Comparative Plot)     │                                │
     │                                │                                │
     │<── 12. emit "tool_call_result"─│                                │
     │    (Structured execution log)  │                                │
     │                                │                                │
     │                                │── 13. Summarize Plan ─────────>│
     │                                │    (Synthesizer LLM)           │
     │                                │                                │
     │<── 14. emit "token" ───────────│                                │
     │    (Word-by-word streaming)    │                                │
     │                                │                                │
     │<── 15. emit "final" ───────────│                                │
     │    (Transaction complete)      │                                │
     │                                │                                │
     │<── 16. Terminate Connection ───│                                │
     │                                │                                │
```

## Status

Scaffold. AlphaGenome track metadata is a placeholder — see [services/alphagenome_svc/data/track_metadata.tsv](services/alphagenome_svc/data/track_metadata.tsv) and [scripts/fetch_track_metadata.py](scripts/fetch_track_metadata.py).

---

## Inverse Genome Editing & Agent Alignment Loop

We have extended OmniGemmaNome with a powerful inverse-design genomic editing engine and a full closed-loop human-feedback + automated alignment (RLAIF) pipeline.

### 🧬 Inverse Genome Editing Engine
An advanced unified solver supports multiple edit styles to achieve target functional objectives (maximizing, minimizing, or matching specific values on regulatory tracks). The solver evaluates proposed edits based on their **Log2 Fold-Change (LFC)** over the no-edit baseline reference signal, rather than raw differential signals, to more accurately represent biological impact. Signal tracking and LFC computation are centered precisely around the edit site by taking the mean signal of the **3 center bins** of the spatial dimension(s) instead of averaging the entire sequence track.

Supported edit styles include:
* **Beam Search (Multi-site SNVs):** Greedily searches combinations of up to $N$ synergistic SNV substitutions evaluated by In-Silico Mutagenesis (ISM) to find optimal multi-site variants.
* **Sliding-Window Deletion Scanner:** Evaluates sliding deletions of 5, 10, 25, or 50 bp, performing in-place replacement padding with local downstream sequence to preserve input sequence size and genomic coordinate alignment for perfect model predictions.
* **Evenly-Spaced Deletions:** If `max_candidates` is limited during deletion sweeps, places exactly $N$ deletions of fixed length (10bp) evenly spaced across the *full input locus* with length-preserving in-place padding.
* **Motif-Aware Insertion / Ablation Solver:** Inserts consensus binding motifs (CTCF, SP1, AP-1, TATA, OCT4, NF-kB) systematically across design region spacing, or searches for and ablates existing motifs (with up to 2 mismatches) using scrambled substitution or deletion.

Registered on the AlphaGenome microservice as `/optimize_edits` (with real-time progress updates streamed via `/optimize_edits/stream` using multi-threaded SSE queues) and exposed to the LangGraph agent as the `optimize_edits` tool.

#### 📐 Automatic Locus Dimension Alignment
To satisfy Deep UNet convolutional downsampling layers and maximum input constraints:
* **Symmetrical Padding:** Loci shorter than 16,384 bp are automatically padded symmetrically to at least 16,384 bp and expanded to the nearest multiple of 2048 bp, eliminating shape mismatch errors (e.g., `EinopsError`).
* **Symmetrical Clipping:** Loci longer than 131,072 bp (such as SNAP25 at ~135kb) are automatically and symmetrically clipped/contracted to exactly 131,072 bp centered on the original region, satisfying the maximum supported sequence length of the underlying predictor model.

### 📊 Web UI Comparison & Alignment Portal
The React web interface is enhanced with deep user feedback controls:
1. **Interactive Zoom Suite with HTML5 Canvas Overlaid Comparison:** Renders reference vs. edited state (bright coral dashed line) overlaid on the same plot. Fully supports multi-modal zooming (from 3 bins up to the full $N$ bins predicted) via click-and-drag subregion selections, symmetrical zoom in/out buttons, direct range input, or keyboard shortcuts (`Cmd/Ctrl` + `+`/`-`). Features local vertical autoscale, synchronized genomic coordinate ticks, and dual-percentage hovercards (zoomed offset vs. full global locus window).
2. **Inline Feedback & Step Corrections:** Thumbs-up/down button logs preferences (capturing structured `chosen_tool_calls` and `rejected_tool_calls` payloads inside preference databases to enable multi-turn RL fine-tuning). Users can edit agent reasoning steps (thoughts) and tool call parameters directly inline before executing them to log golden-standard Supervised Fine-Tuning (SFT) training data.
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

