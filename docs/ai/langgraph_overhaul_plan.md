# OmniGemmaNome — LangGraph Overhaul Plan

## Context

The current system uses smolagents with a flat ToolCallingAgent. This works for simple single-tool questions but has three structural limits that block the full task set in `user_tasks.md`:

1. **No typed state** — session context leaks through a side-channel (`session_context` on the tool object); prediction caches, viz specs, and uploads are unstructured attributes on a Session dataclass rather than first-class graph state.
2. **Fragile streaming** — the EventBus + thread-executor pattern (blocking agent.run() in a thread pool, feeding an asyncio queue) is brittle and hard to extend.
3. **Flat tool list** — small macros cover ~5 of the ~25 task types in `user_tasks.md`; the remaining task types (variant databases, regulatory elements, ENCODE, GTEx, GWAS, splicing, 3D contacts, CRISPR, attribution, comparative analysis) have no tooling.

**LangGraph replaces smolagents** for a proper typed StateGraph with native async streaming, conditional routing, and a ToolNode that the same Gemma-4B model calls into. The macro pattern (one LLM call → one macro tool = full workflow) is preserved because small models can't plan multi-step chains reliably. All existing tools are reused — only the orchestration layer changes.

**Phased rollout**: Phase 1 (core migration), Phase 2 (public API knowledge tools), Phase 3 (advanced AlphaGenome analysis tools + new svc endpoints).

---

## Architecture

### Graph Topology

```
START
  └─► intent_router_node
        ├── "answer" ──► conversational_node ──► END
        └── "predict" ──► agent_node ◄──────────────────┐
                              │                          │
                              ▼ (tool_calls present)     │
                         tool_node ──────────────────────┘
                              │ (no tool_calls / max_steps)
                              ▼
                         synthesizer_node ──► END
```

- **intent_router_node**: LLM call classifying "answer" vs "predict". Replaces `router.py:classify()`.
- **conversational_node**: Token-streaming direct answer for non-predict queries. Replaces `router.py:stream_conversational()`.
- **agent_node**: LLM reasoning step — sees full tool list, emits tool calls. The core ReAct loop.
- **tool_node**: LangGraph `ToolNode` — executes all tool calls (parallel when LLM emits multiple).
- **synthesizer_node**: Post-processing node — collects `viz_specs` from state, emits `viz_spec` SSE events, formats final answer.

### Typed State

```python
# services/agent_backend/state.py
class AgentState(TypedDict):
    messages:          Annotated[list[AnyMessage], add_messages]
    session_id:        str
    intent:            Literal["answer", "predict"] | None
    prediction_cache:  dict        # key → {prediction_id, locus, arrays, resolution, organism}
    viz_specs:         Annotated[list[dict], operator.add]
    uploads:           dict        # upload_id → {sequence, source}
    last_prediction:   dict | None
```

`prediction_cache` replaces the ad-hoc `session_context.predict_cache` dict in macros.py. `viz_specs` replaces `session_context.pending_viz_spec`. Both are now deduplicated by LangGraph's reducer semantics.

### Streaming (replaces EventBus thread hack)

```python
# app.py - new /chat handler
async for event in graph.astream_events(initial_state, config=rconfig, version="v2"):
    match event["event"]:
        case "on_chat_model_stream":
            yield sse("token", {"text": event["data"]["chunk"].content})
        case "on_tool_start":
            yield sse("tool_call_start", {"name": event["name"]})
        case "on_tool_end":
            yield sse("tool_call_result", {"name": event["name"], "output": event["data"]["output"]})
        case "on_chain_end" if event["name"] == "synthesizer_node":
            for vs in event["data"]["output"].get("viz_specs", []):
                yield sse("viz_spec", vs)
            yield sse("final", {"answer": ...})
```

No threads, no asyncio queue, no EventBus. `astream_events` is native async.

### Tool Wrapping Strategy

Existing `macros.py` logic is **unchanged**. An adapter layer wraps each macro as a LangChain `StructuredTool` with `RunnableConfig` injection for session access:

```python
# services/agent_backend/tools/langchain_adapter.py
from langchain_core.tools import StructuredTool
from langchain_core.runnables import RunnableConfig

def _adapt(macro_cls, name, description, schema):
    def _fn(config: RunnableConfig, **kwargs) -> dict:
        session_id = config["configurable"]["session_id"]
        session = STORE.get_or_create(session_id)
        # sync prediction_cache from graph state into session (for caching)
        return macro_cls(session_context=session).forward(**kwargs)
    return StructuredTool.from_function(_fn, name=name, description=description,
                                        args_schema=schema, infer_schema=False)
```

All 13 existing tools (5 macros + 8 primitives) are adapted this way. `SessionAwareTool` base class and `macros.py` remain untouched.

---

## Phase 1 — Core Migration (LangGraph backbone)

### New Files

| File | Purpose |
|------|---------|
| `services/agent_backend/state.py` | `AgentState` TypedDict |
| `services/agent_backend/graph.py` | `build_graph()` → compiled `StateGraph` |
| `services/agent_backend/nodes/__init__.py` | node package |
| `services/agent_backend/nodes/intent_router.py` | intent classification node |
| `services/agent_backend/nodes/conversational.py` | token-streaming direct answer node |
| `services/agent_backend/nodes/agent_node.py` | ReAct LLM step node |
| `services/agent_backend/nodes/synthesizer.py` | viz emit + final answer node |
| `services/agent_backend/tools/langchain_adapter.py` | wraps existing tools for LangGraph ToolNode |

### Modified Files

| File | Change |
|------|--------|
| `services/agent_backend/app.py` | Replace `build_agent()+agent.run()` + EventBus with `build_graph()` + `graph.astream_events()` |
| `services/agent_backend/tools/__init__.py` | Export `build_tool_list()` returning `list[StructuredTool]` |
| `pyproject.toml` (agent_backend) | Add `langgraph>=0.2`, `langchain-core>=0.3`, `langchain-ollama>=0.2`; keep `litellm` for now |

### Preserved Unchanged
- `services/agent_backend/sessions.py`
- `services/agent_backend/streaming.py` (EventBus kept for fallback, unused in main path)
- `services/agent_backend/ollama_model.py` (wrapped as `ChatOllama` or kept via litellm)
- `services/agent_backend/tools/macros.py`
- `services/agent_backend/tools/_base.py`
- `services/agent_backend/tools/` (all existing tool files)
- All of `services/alphagenome_svc/`

### LLM Node

Use `langchain-ollama` `ChatOllama` bound with tools:
```python
llm = ChatOllama(model=cfg.ollama.model, base_url=cfg.ollama.base_url, temperature=0.1)
llm_with_tools = llm.bind_tools(tools)
```
`TolerantLiteLLMModel` is not needed in this path — LangChain's Ollama integration handles tool call parsing natively.

---

## Phase 2 — Public API Knowledge Tools

All are pure HTTP wrappers calling public, unauthenticated REST/GraphQL APIs. Each is a new `StructuredTool` and a corresponding high-level macro in `macros.py`.

### New Tool Files

| File | Tool Name | API | Purpose |
|------|-----------|-----|---------|
| `tools/clinvar.py` | `query_clinvar` | NCBI eutils (`eutils.ncbi.nlm.nih.gov`) | ClinVar pathogenicity + clinical significance for variants in a locus |
| `tools/gnomad.py` | `query_gnomad` | gnomAD GraphQL (`gnomad.broadinstitute.org/api`) | Population allele frequencies, constraint scores per gene |
| `tools/gwas.py` | `query_gwas_catalog` | EBI GWAS REST (`www.ebi.ac.uk/gwas/rest/api`) | GWAS associations for variants in a locus |
| `tools/encode.py` | `query_encode_elements` | ENCODE REST (`www.encodeproject.org`) | Known regulatory elements (cCREs) overlapping a locus |
| `tools/gtex.py` | `query_gtex_expression` | GTEx Portal API (`gtexportal.org/api/v2`) | Tissue-level gene expression + eQTL associations |
| `tools/conservation.py` | `query_conservation` | UCSC API (`api.genome.ucsc.edu`) | phyloP / phastCons conservation scores for a locus |
| `tools/jaspar.py` | `scan_jaspar_motifs` | JASPAR REST (`jaspar.genereg.net/api/v1`) | TF motif instances in a DNA sequence |

### New Macros (added to `macros.py`)

| Macro Class | Composes | Answers |
|-------------|----------|---------|
| `AnalyzeVariantClinical` | `query_clinvar` + `query_gnomad` + `analyze_variant_effect` | "What variants are in gene X and are they pathogenic?" |
| `AnalyzeGwasAssociations` | `query_gwas_catalog` + gene lookup | "What traits are associated with variants near gene X?" |
| `AnalyzeKnownRegulatoryElements` | `query_encode_elements` + `analyze_region_regulation` | "What regulatory elements are known in this region?" |
| `AnalyzeGtexExpression` | `query_gtex_expression` | "What tissues express gene X (from GTEx experimental data)?" |

These complement the existing AlphaGenome-based expression tools — GTEx gives experimental validation context.

### Config additions
- `config/tools.yaml`: add toggles for all new tools (enabled: true for macros, false for primitives)

---

## Phase 3 — Advanced AlphaGenome Analysis Tools

These use AlphaGenome heads that are already predicted but not yet surfaced as macros, plus two new `alphagenome_svc` endpoints.

### New Tool Files

| File | Macro Class | Uses | Answers |
|------|------------|------|---------|
| `tools/splice.py` | `AnalyzeSplicing` | `splice_sites`, `splice_junctions`, `splice_site_usage` heads | "What are the predicted splicing patterns of gene X?" |
| `tools/contacts.py` | `Analyze3dContacts` | `contact_maps` head | "What distal elements interact with gene X? What are the 3D chromatin contacts?" |
| `tools/comparative.py` | `AnalyzeDifferentialRegulation` | AlphaGenome `/predict` × 2 tissue-filtered track sets | "How does BRCA1 regulation differ between breast and liver?" |
| `tools/tiled_scan.py` | `AnalyzeLargeRegion` | Tiled AlphaGenome calls + stitching | "What is the regulatory landscape across this 1Mb region?" |
| `tools/crispr.py` | `DesignCrisprGuide` | Sequence scan (PAM sites) + `chip_tf`/`atac`/`cage` heads for on-target scoring | "Design a CRISPR guide to target gene X" |

### New `alphagenome_svc` Endpoints (for Phase 3 advanced tools)

| Endpoint | Purpose | Implementation |
|----------|---------|----------------|
| `POST /attribution` | Integrated gradients over a locus/sequence for a head+track | `captum` or manual IG loop over backbone |
| `POST /optimize_sequence` | Gradient ascent on sequence for a target track signal | Adam optimizer over one-hot embedding |

These are needed for attribution analysis (`AnalyzeAttribution`) and sequence design (`OptimizeSequence`). Both require backprop through the model and must run in the alphagenome_svc process where the model lives.

---

## File Map (all three phases)

```
services/agent_backend/
  state.py                         NEW  (Phase 1)
  graph.py                         NEW  (Phase 1)
  app.py                           MOD  (Phase 1)
  agent.py                         DEL  (replaced by graph.py)
  router.py                        DEL  (replaced by nodes/intent_router.py + nodes/conversational.py)
  streaming.py                     KEEP (EventBus preserved, no longer used in main path)
  sessions.py                      KEEP
  ollama_model.py                  KEEP (may wrap for LangChain compat)
  _variant_utils.py                KEEP
  nodes/
    __init__.py                    NEW  (Phase 1)
    intent_router.py               NEW  (Phase 1)
    conversational.py              NEW  (Phase 1)
    agent_node.py                  NEW  (Phase 1)
    synthesizer.py                 NEW  (Phase 1)
  tools/
    _base.py                       KEEP
    macros.py                      KEEP + extend (Phases 2 & 3 add new macro classes)
    alphagenome.py                 KEEP
    genome_lookup.py               KEEP
    variants.py                    KEEP
    tracks.py                      KEEP
    uploads.py                     KEEP
    viz.py                         KEEP
    __init__.py                    MOD  (Phase 1: export StructuredTools; Phases 2/3: register new)
    langchain_adapter.py           NEW  (Phase 1)
    clinvar.py                     NEW  (Phase 2)
    gnomad.py                      NEW  (Phase 2)
    gwas.py                        NEW  (Phase 2)
    encode.py                      NEW  (Phase 2)
    gtex.py                        NEW  (Phase 2)
    conservation.py                NEW  (Phase 2)
    jaspar.py                      NEW  (Phase 2)
    splice.py                      NEW  (Phase 3)
    contacts.py                    NEW  (Phase 3)
    comparative.py                 NEW  (Phase 3)
    tiled_scan.py                  NEW  (Phase 3)
    crispr.py                      NEW  (Phase 3)
    attribution.py                 NEW  (Phase 3)
    sequence_design.py             NEW  (Phase 3)

services/alphagenome_svc/
  app.py                           MOD  (Phase 3: add /attribution, /optimize_sequence endpoints)
  schemas.py                       MOD  (Phase 3: add AttributionRequest/Response, OptimizeRequest/Response)
  model_loader.py                  MOD  (Phase 3: add compute_attribution(), optimize_sequence() methods)
  [everything else]                KEEP

config/
  tools.yaml                       MOD  (add new tool toggles per phase)
  schema/settings.py               KEEP (AgentConfig.agent_type no longer used; remove in cleanup)
```

---

## Verification

### Phase 1
1. `python -c "from services.agent_backend.graph import build_graph; g = build_graph(); print(g.get_graph().draw_ascii())"` — graph compiles and topology is correct
2. `curl -N -X POST /chat -d '{"message":"What are the epigenetic properties of BRCA1?"}'` → SSE stream contains `tool_call_start`, `tool_call_result`, `viz_spec`, `final` events in order
3. `curl -N -X POST /chat -d '{"message":"Hello"}'` → SSE stream takes the `answer` branch (no tool events, only `token` + `final`)
4. Send same AlphaGenome query twice in one session → second call hits `prediction_cache` (verify via `alphagenome_cached` progress event)

### Phase 2
5. `curl -N -X POST /chat -d '{"message":"What ClinVar variants are in BRCA1?"}'` → response cites ClinVar entries with clinical significance
6. `curl -N -X POST /chat -d '{"message":"What tissues express TP53 according to GTEx?"}'` → response includes GTEx tissue data distinct from AlphaGenome predictions

### Phase 3
7. `curl -N -X POST /chat -d '{"message":"What are the predicted splicing patterns of NRXN1 in brain?"}'` → splice_sites/junctions heads appear in tool result
8. `curl -N -X POST /chat -d '{"message":"Design a CRISPR guide to knock out BRCA1 exon 11"}'` → response includes ranked guide candidates with predicted on-target scores
9. `POST /attribution {"locus":"chr17:43044295-43125483","head":"chip_tf","track_index":0}` → returns per-position importance scores
