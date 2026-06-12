# Configuration Reference

All non-secret config lives in [config/](../config/) as YAML. Secrets live in `.env`.

## Load order

[config/schema/settings.py](../config/schema/settings.py) merges sources in this order:

1. `config/settings.yaml` — defaults.
2. `config/settings.{APP_ENV}.yaml` — overlay for the selected environment (`dev` or `prod`). `APP_ENV` defaults to `dev`.
3. `config/{paths,models,huggingface,ollama,alphagenome,ensembl,agent,tools}.yaml` — domain-specific files, one per top-level key.
4. Environment variables with prefix `ALPHAGEMMA_` and nested-delimiter `__`. Example: `ALPHAGEMMA_ALPHAGENOME__DEVICE=cuda` sets `alphagenome.device`.

The merged dict is validated against the `Settings` Pydantic model and cached via `functools.lru_cache`.

Calling:

```python
from schema import load_settings
settings = load_settings()
```

...returns the same cached `Settings` instance for the lifetime of the process.

## Secrets (`.env`)

Only two keys, both optional:

```bash
APP_ENV=dev                    # or "prod" — selects the overlay YAML
HUGGINGFACE_HUB_TOKEN=hf_...   # required only for gated HF models (default model is public)
```

## `settings.yaml`

Top-level defaults.

```yaml
app_env: dev

server:
  agent_backend_port: 8000
  alphagenome_svc_port: 8001
  cors_origins:
    - http://localhost:5173

limits:
  max_sequence_length: 131072   # AlphaGenome input size; model-enforced, not a dev knob
  max_upload_bytes: 10485760    # 10 MiB
  session_ttl_seconds: 3600
```

- **`max_sequence_length`** — 131,072 is the AlphaGenome model cap. Lowering this just rejects valid inputs early; raising it breaks the model.
- **`cors_origins`** — production overlay sets this to `[]`, which means the FastAPI middleware falls back to `["*"]` (see the `or ["*"]` in both app files). Tighten manually for a real deployment.

## `paths.yaml`

```yaml
genome_fasta: services/alphagenome_svc/data/hg38.fa
genome_fai: services/alphagenome_svc/data/hg38.fa.fai
alphagenome_weights: services/alphagenome_svc/data/model_all_folds.safetensors
track_metadata_tsv: services/alphagenome_svc/data/track_metadata.tsv
upload_dir: services/alphagenome_svc/data/uploads
session_store_dir: .sessions
prompts_dir: services/agent_backend/prompts
```

Paths are relative to the repo root and resolved to absolute paths at load time via `PathsConfig.resolve(PROJECT_ROOT)`. Shell scripts read this file directly via [scripts/_paths.py](../scripts/_paths.py) so `./scripts/download_genome.sh` and the service agree on where `hg38.fa` lives.

## `models.yaml`

```yaml
huggingface:
  alphagenome:
    repo_id: gtca/alphagenome_pytorch
    filename: model_all_folds.safetensors
    revision: main

ollama:
  model: qwen2.5:7b-instruct    # overridden by config/ollama.yaml — models.yaml is legacy

organism_index:
  human: 0
  mouse: 1
```

`organism_index` is passed to AlphaGenome's `predict(organism_index=…)` call. The effective Ollama model is whatever is set in [config/ollama.yaml](../config/ollama.yaml), not this file (this one is vestigial).

## `huggingface.yaml`

```yaml
cache_dir: ~/.cache/huggingface
token_env_var: HUGGINGFACE_HUB_TOKEN
offline: false
```

The `~` is expanded at load time.

## `ollama.yaml`

```yaml
base_url: http://localhost:11434
model: qwen3:4b
temperature: 0.1
max_tokens: 4096
request_timeout_seconds: 240
```

- **`model`** — any Ollama-served model. Default is `qwen3:4b` because it emits a `<think>` block that the UI renders as collapsible reasoning (see [frontend.md](frontend.md)). Switching to `gemma:2b` or `llama3.1:8b` works but those models won't produce the `reasoning_content` field.
- **`max_tokens: 4096`** — needs to cover the `<think>` block plus the final answer. Dropping below ~2048 causes Qwen3 to truncate mid-thought and emit no tool call.
- **`request_timeout_seconds: 240`** — higher than you'd expect because Qwen3's reasoning can take ~60–90s on CPU for a gnarly question, plus the LiteLLM client has no streaming in the tool-call loop.

## `alphagenome.yaml`

```yaml
service_url: http://localhost:8001
default_heads:
  - rna_seq
  - chip_tf
  - atac
default_resolution: 128bp
max_input_length: 131072
device: cpu
batch_size: 1
dtype: float16
```

- **`default_heads`** — the set returned when the agent/`predict_tracks` doesn't specify. Available: `atac, dnase, procap, cage, rna_seq, chip_tf, chip_histone, contact_maps, splice_sites, splice_junctions, splice_site_usage`.
- **`default_resolution`** — `128bp` is roughly 1000× smaller tensors than `1bp` and sufficient for every current viz panel.
- **`device`** — `cpu` / `cuda` / `mps`. Override with `ALPHAGEMMA_ALPHAGENOME__DEVICE=cuda`.
- **`dtype`** — declared but not currently threaded through to weight-loading; pytorch will run at whatever the checkpoint was saved as.

## `ensembl.yaml`

```yaml
rest_base_url: https://rest.ensembl.org
assembly: GRCh38
request_timeout_seconds: 15
rate_limit_per_second: 10
```

`rate_limit_per_second` is declared but not currently enforced — the codebase makes one Ensembl call per `gene_to_locus` invocation and never hits the limit.

## `agent.yaml`

```yaml
max_steps: 6
agent_type: tool_calling
retry_on_parse_error: 2
verbose: true
```

- **`max_steps`** — smolagents stops after 6 reasoning steps. With macros (one tool → `final_answer`), even retries on parse failure stay under this.
- **`agent_type`** — only `tool_calling` is actually used. `code` is reserved for future `CodeAgent` support.

## `tools.yaml`

Per-tool toggles. Defaults:

```yaml
# Macros (enabled)
analyze_gene_tf_binding: {enabled: true}
analyze_region_regulation: {enabled: true}
analyze_variant_effect: {enabled: true}
upload_sequence: {enabled: true}

# Primitives (disabled)
gene_to_locus: {enabled: false}
predict_tracks: {enabled: false}
predict_variant_effect: {enabled: false}
list_tracks_by_assay: {enabled: false}
get_top_tracks: {enabled: false}
render_panel: {enabled: false}
parse_hgvs: {enabled: false}

# Planned stubs (disabled, no implementation yet)
ensembl_regulatory_build: {enabled: false}
jaspar_motif_scan: {enabled: false}
gtex_expression: {enabled: false}
conservation_score: {enabled: false}
clinvar_lookup: {enabled: false}
```

See [agent-tools.md](agent-tools.md) for what each does. The stub entries won't surface even if enabled — there's no class registered for them in [tools/__init__.py](../services/agent_backend/tools/__init__.py).

## `logging.yaml`

Standard `logging.config.dictConfig` shape. Defaults:

```yaml
version: 1
disable_existing_loggers: false
root:
  level: INFO
loggers:
  services.alphagenome_svc:
    level: INFO
  services.agent_backend:
    level: DEBUG
  smolagents:
    level: INFO
```

Not auto-applied — import `logging.config.dictConfig(yaml.safe_load(open('config/logging.yaml')))` manually if you want these levels. By default both FastAPI apps inherit uvicorn's logger config.

## Pydantic schema

Defined in [config/schema/settings.py](../config/schema/settings.py):

```
Settings
├── app_env: "dev" | "prod"
├── server: ServerConfig
│   ├── agent_backend_port: int
│   ├── alphagenome_svc_port: int
│   └── cors_origins: list[str]
├── limits: LimitsConfig
│   ├── max_sequence_length: int
│   ├── max_upload_bytes: int
│   └── session_ttl_seconds: int
├── paths: PathsConfig         (7 Path fields — all resolved absolute)
├── models: ModelsConfig
│   ├── huggingface: dict[str, HFModelEntry]
│   ├── ollama: dict[str, str]
│   └── organism_index: dict[str, int]
├── huggingface: HuggingFaceConfig
├── ollama: OllamaConfig
├── alphagenome: AlphaGenomeConfig
├── ensembl: EnsemblConfig
├── agent: AgentConfig
└── tools: dict[str, ToolToggle]
```

Every field has a default except the ones in `paths`, `models`, `huggingface`, `ollama`, `alphagenome`, `ensembl`, `agent`, and `tools` — those are required to come from YAML. Missing a YAML file produces a Pydantic validation error at startup, which is the intended behavior (fail loudly rather than silently use defaults for a deployment-critical value like `alphagenome_weights`).

## Environment overrides (quick reference)

| Variable | Meaning |
|---|---|
| `APP_ENV` | `dev` or `prod` — picks the overlay |
| `ALPHAGEMMA_OLLAMA__BASE_URL` | override Ollama URL (docker-compose uses this) |
| `ALPHAGEMMA_OLLAMA__MODEL` | swap model without editing YAML |
| `ALPHAGEMMA_ALPHAGENOME__SERVICE_URL` | point agent-backend at a remote alphagenome-svc |
| `ALPHAGEMMA_ALPHAGENOME__DEVICE` | `cpu` / `cuda` / `mps` |
| `ALPHAGEMMA_AGENT__MAX_STEPS` | cap agent reasoning steps |
| `HUGGINGFACE_HUB_TOKEN` | for gated HF downloads |
