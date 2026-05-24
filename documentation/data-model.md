# Data Model Reference

Schemas for the wire, the database (in-memory), the agent, and the UI. All data shapes worth knowing.

## Python: [config/schema/settings.py](../config/schema/settings.py)

`Settings` is the root Pydantic model. See [configuration.md](configuration.md) for field-by-field meaning. The types here are load-time validated; missing required fields fail at startup.

## Python: [services/alphagenome_svc/schemas.py](../services/alphagenome_svc/schemas.py)

### Literals

```python
Head = Literal[
    "atac", "dnase", "procap", "cage", "rna_seq",
    "chip_tf", "chip_histone", "contact_maps",
    "splice_sites", "splice_junctions", "splice_site_usage",
]
Resolution = Literal["1bp", "128bp"]
Organism   = Literal["human", "mouse"]
```

### `PredictRequest`

```python
class PredictRequest(BaseModel):
    locus: str | None = None
    sequence: str | None = None
    organism: Organism = "human"
    heads: list[Head] = ["rna_seq", "chip_tf", "atac"]
    resolution: Resolution = "128bp"

    @model_validator(mode="after")
    def _exactly_one_source(self): ...
```

Invariant: provide exactly one of `locus` or `sequence`. Violating this raises a 422 at the HTTP boundary.

### `TrackArray`

```python
class TrackArray(BaseModel):
    head: Head
    shape: list[int]     # e.g. [1, 1024, 1664]
    dtype: str           # "float16" in practice
    data_b64: str        # base64(np.ascontiguousarray(...).tobytes())
    track_indices: list[int]
```

Shape conventions:

- 1D signal heads (`atac`, `dnase`, `procap`, `cage`, `rna_seq`, `chip_tf`, `chip_histone`, `splice_site_usage`, `splice_junctions`) → `[batch=1, positions, tracks]`.
- `splice_sites` → similar but small track count (5).
- `contact_maps` → higher-rank (typically `[batch, positions, positions, tracks=28]`), NOT supported by the 1D track endpoint.

`track_indices` is always `[0, 1, ..., n_tracks-1]` — there's no gapped indexing.

### `PredictResponse`

```python
class PredictResponse(BaseModel):
    prediction_id: str   # uuid4
    locus: str | None
    organism: Organism
    resolution: Resolution
    arrays: list[TrackArray]
```

### `TrackInfo`

Row from [track_metadata.tsv](../services/alphagenome_svc/data/track_metadata.tsv), matching the DeepMind published schema:

```python
class TrackInfo(BaseModel):
    organism: Organism
    head: Head                 # derived from TSV's output_type (uppercase → lowercase)
    track_index: int
    name: str
    strand: str | None
    assay_title: str | None    # "ChIP-seq", "ATAC-seq", "RNA-seq", ...
    file_assembly: str | None
    data_source: str | None    # "ENCODE", "GTEx", ...
    target_label: str | None   # TF name for chip_tf, histone mark for chip_histone
    ontology_curie: str | None
    biosample_name: str | None
    biosample_type: str | None
    gtex_tissue: str | None
    gtex_tissue_group: str | None
    experiment_accession: str | None
    file_accession: str | None
    frip: float | None
    nonzero_mean: float | None
```

Tissue matching in macros uses `biosample_name`, `gtex_tissue`, `gtex_tissue_group`. TF/target annotation uses `target_label`.

### `HealthResponse`

```python
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model_loaded: bool
    genome_indexed: bool
    track_metadata_loaded: bool
    device: str
```

---

## Python: [services/agent_backend/sessions.py](../services/agent_backend/sessions.py)

### `Session`

In-memory session. Dataclass, not Pydantic.

```python
@dataclass
class Session:
    id: str
    created_at: float
    last_used_at: float
    history: list[dict[str, Any]]            # chat turns for the conversational router
    uploads: dict[str, dict[str, Any]]       # upload_id → {sequence: str, source: str}
    last_prediction: dict[str, Any] | None   # see below
    pending_viz_spec: dict[str, Any] | None  # picked up by step_callback → SSE event
    bus: Any                                  # EventBus for the current /chat call
```

Fields populated dynamically (not in dataclass):

- `predict_cache: dict[tuple, dict]` — see [agent-tools.md#side-effects-on-the-session](agent-tools.md#side-effects-on-the-session).

### `last_prediction` shape

```python
{
    "prediction_id": "uuid",
    "locus": "chr17:43044295-43175367",   # or None for sequence-based
    "arrays": {
        "chip_tf": np.ndarray,             # decoded from base64
        "atac":    np.ndarray,
        ...
    },
    "resolution": "128bp",
    "organism": "human",
}
```

The arrays here are post-`_decode` numpy — no batch-dim handling at this layer. The tracks endpoint in [app.py](../services/agent_backend/app.py) is responsible for squeezing `(1, positions, tracks) → (positions, tracks)` before returning to the frontend.

---

## Python: Agent request/response

```python
class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None

class UploadResponse(BaseModel):
    upload_id: str
    length: int
    session_id: str

class HealthResponse(BaseModel):
    status: str
    ollama_reachable: bool
    alphagenome_reachable: bool
```

---

## TypeScript: [frontend/src/types/viz_spec.ts](../frontend/src/types/viz_spec.ts)

### `VizSpec`

```ts
type VizPanelType = "igv_tracks" | "contact_map" | "splice_arcs" | "variant_delta";

interface VizSpec {
  type: VizPanelType;
  prediction_id: string;
  locus: string | null;
  head: string | null;
  track_indices: number[] | null;
}
```

Emitted by the backend in SSE `viz_spec` events. The frontend treats unknown types as JSON-fallback.

### `ChatEventType`

```ts
type ChatEventType =
  | "token" | "thought" | "progress"
  | "tool_call_start" | "tool_call_result"
  | "viz_spec" | "error" | "final";
```

Matches the Python `EventType` in [streaming.py](../services/agent_backend/streaming.py).

### `ToolCall`

```ts
interface ToolCall {
  tool: string;
  status: "pending" | "done" | "error";
  arguments?: unknown;   // dict, or JSON string — depends on LLM formatting
  observation?: string;  // tool return, stringified, truncated to 4000 chars
}
```

### `ProgressEntry`

```ts
interface ProgressEntry {
  stage: string;   // "alphagenome_start", "alphagenome_done", "alphagenome_cached", "alphagenome_error"
  text: string;
}
```

The UI color-codes by stage suffix (`_done` → green, `_error` → red, else amber).

### `ChatMessage`

```ts
interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  thoughts?: string;
  toolCalls?: ToolCall[];
  progress?: ProgressEntry[];
  streaming?: boolean;
}
```

One per chat bubble. User messages use only `content`. Assistant messages accumulate all fields as SSE frames arrive.

---

## TypeScript: [frontend/src/api/tracks.ts](../frontend/src/api/tracks.ts)

### `TrackMetadata`

Subset of `TrackInfo` that the tracks endpoint returns (the backend passes through whatever alphagenome-svc sends):

```ts
interface TrackMetadata {
  track_index: number;
  head?: string;
  assay_title?: string;
  target_label?: string;
  biosample_name?: string;
  biosample_type?: string;
  gtex_tissue?: string;
  gtex_tissue_group?: string;
  name?: string;
}
```

### `TrackRow` and `TracksResponse`

```ts
interface TrackRow {
  track_index: number;
  values: number[];        // 4-decimal-rounded
  max: number;
  mean: number;
  metadata: TrackMetadata | null;
}

interface TracksResponse {
  prediction_id: string;
  head: string;
  locus: string | null;
  resolution: string | null;
  positions: number;       // original array length
  downsampled_to: number;  // values.length per row
  tracks: TrackRow[];
}
```

---

## Track metadata TSV schema

[services/alphagenome_svc/data/track_metadata.tsv](../services/alphagenome_svc/data/track_metadata.tsv) — tab-separated, with a header row. Columns (expected by the loader in [app.py](../services/alphagenome_svc/app.py)):

```
organism, output_type, name, strand, track_index, Assay title,
File assembly, data_source, Target label, ontology_curie,
biosample_name, biosample_type, gtex_tissue, gtex_tissue_group,
Experiment accession, File accession, frip, nonzero_mean
```

- `output_type` is uppercase (`RNA_SEQ`, `CHIP_TF`), mapped to lowercase `head` at load time via `_OUTPUT_TYPE_TO_HEAD`.
- `track_index` must be unique within `(organism, head)` and match the AlphaGenome tensor's last-axis index.
- Numeric columns (`frip`, `nonzero_mean`) tolerate empty strings → `None`.
- Rows with unknown `output_type` are silently skipped (logged at WARN).

Row counts expected (DeepMind-published; must match AlphaGenome output shapes):

| Head | Tracks |
|---|---|
| atac | 256 |
| dnase | 384 |
| procap | 128 |
| cage | 640 |
| rna_seq | 768 |
| chip_tf | 1,664 |
| chip_histone | 1,152 |
| contact_maps | 28 |
| splice_sites | 5 |
| splice_junctions | 734 |
| splice_site_usage | 734 |
