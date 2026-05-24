# Agent Tool Reference

The agent backend exposes a suite of tools to the LLM via [smolagents](https://github.com/huggingface/smolagents). Two tiers:

- **Macros** — one tool = one full pipeline. The agent only has to pick one per question. Default-enabled in [config/tools.yaml](../config/tools.yaml).
- **Primitives** — chainable building blocks. Default-disabled because a small LLM cannot plan multi-step chains reliably; enable them only with a stronger model.

All tools live in [services/agent_backend/tools/](../services/agent_backend/tools/) and are registered in [tools/__init__.py](../services/agent_backend/tools/__init__.py). Each tool subclasses `smolagents.Tool` and is constructed with `session_context` (the current `Session`).

## Routing rules (system prompt)

[prompts/system.md](../services/agent_backend/prompts/system.md) tells the LLM:

| User asks about… | Call |
|---|---|
| TF binding near a gene (± tissue) | `analyze_gene_tf_binding` |
| Regulatory state of a locus `chrN:start-end` | `analyze_region_regulation` |
| Effect of a variant `chrN:g.POSREF>ALT` | `analyze_variant_effect` |
| A user-uploaded sequence | `upload_sequence` |

Rules enforced in the prompt:

1. **One workflow tool per question.** Either `final_answer` directly (for knowledge questions) or one macro, then `final_answer`.
2. **Every turn is a tool call.** Free-form prose is forbidden — the model calls `final_answer(answer="...")` for any user-facing text.
3. **Pass tissue keywords when relevant.** If the user mentions "brain", pass `tissue_keywords=["brain","cortex",...]`.
4. **Cite the numbers from the tool output.** Track indices, mean signals, deltas, `target_label` (TF name), loci — all must come from the observation, not be invented.

---

## Macros

### `analyze_gene_tf_binding`

Find transcription-factor binding predictions around a gene.

**Source**: [macros.py:255-323](../services/agent_backend/tools/macros.py#L255-L323)

**Inputs**:

| Name | Type | Default | Meaning |
|---|---|---|---|
| `gene_symbol` | string | — | HGNC symbol (e.g. `ASPM`, `BRCA1`) |
| `tissue_keywords` | list\<string\> | null | Tissue substrings to filter tracks (`["brain","cortex","neural"]`) |
| `top_k` | int | 10 | Number of top tracks to return after filtering |
| `organism` | string | `"human"` | `"human"` or `"mouse"` |

**Pipeline**:

1. `_ensembl_lookup(gene_symbol, organism)` → `{chrom, start, end, strand, tss}`.
2. `gene_center = (start + end) // 2`. The window is centered on the gene midpoint, **not** the TSS — this corrects a minus-strand bug where the TSS sits at an endpoint, shifting the window off the gene body (see [macros.py:300-301](../services/agent_backend/tools/macros.py#L300-L301)).
3. `_window_around(chrom, gene_center, cfg.max_input_length)` — builds `chrN:start-end` with length 131,072 bp centered on `gene_center`.
4. `_predict(locus, None, ["chip_tf"], "128bp", organism, session)` — session-cached.
5. Rank top `3 × top_k` tracks by mean signal, annotate with metadata from `GET /tracks?head=chip_tf`, filter by `tissue_keywords` via substring match against `biosample_name`, `gtex_tissue`, `gtex_tissue_group`. If the filter eliminates all tracks, fall back to the unfiltered top list and add a note.
6. Trim to `top_k` and emit an `igv_tracks` viz_spec.

**Output**:

```json
{
  "gene": {"gene_symbol": "ASPM", "ensembl_id": "...", "chrom": "chr1", "start": 197084121, "end": 197146694, "strand": "-", "tss": 197146694},
  "window": "chr1:197049871-197180943",
  "head": "chip_tf",
  "top_tracks": [
    {"track_index": 1204, "mean_signal": 0.74, "target_label": "CTCF", "biosample_name": "brain_cortex", ...},
    ...
  ],
  "note": null,
  "viz_spec": {"type": "igv_tracks", "prediction_id": "...", "locus": "chr1:197049871-197180943", "head": "chip_tf", "track_indices": [...]}
}
```

---

### `analyze_region_regulation`

Profile the chromatin-accessibility and histone-mark landscape of a locus.

**Source**: [macros.py:326-394](../services/agent_backend/tools/macros.py#L326-L394)

**Inputs**:

| Name | Type | Default | Meaning |
|---|---|---|---|
| `locus` | string | — | `chrN:start-end` |
| `heads` | list\<string\> | `["atac","dnase","chip_histone"]` | Heads to profile |
| `top_k` | int | 5 | Top tracks per head |
| `organism` | string | `"human"` | `"human"` / `"mouse"` |

**Pipeline**:

1. `_predict(locus, None, heads, "128bp", organism, session)` — one forward pass, all heads in one go.
2. For each head: rank top `top_k` tracks by mean signal, annotate from `/tracks`.
3. Emit `igv_tracks` viz_spec anchored on the first head in `heads`.

**Output** (abbreviated):

```json
{
  "locus": "chr17:43044295-43175000",
  "heads": ["atac","dnase","chip_histone"],
  "top_tracks_by_head": {
    "atac": [{"track_index": 112, "mean_signal": 1.3, "assay_title": "ATAC-seq", ...}],
    "dnase": [...],
    "chip_histone": [...]
  },
  "notes": [],
  "viz_spec": {"type": "igv_tracks", "head": "atac", ...}
}
```

---

### `analyze_variant_effect`

Predict functional impact of a single-base substitution.

**Source**: [macros.py:397-483](../services/agent_backend/tools/macros.py#L397-L483)

**Inputs**:

| Name | Type | Default | Meaning |
|---|---|---|---|
| `variant_hgvs` | string | — | Genomic HGVS SNV, e.g. `chr17:g.43044295A>G`. Only SNVs supported. |
| `heads` | list\<string\> | `["atac","chip_tf"]` | Heads to compare |
| `window_bp` | int | `alphagenome.max_input_length` | Window size around the variant |
| `organism` | string | `"human"` | `"human"` / `"mouse"` |

**Pipeline**:

1. Regex-parse the HGVS string.
2. `_window_around(chrom, pos, window_bp)` — centers on the variant position.
3. REF pass: `_predict(locus, None, heads, …)`.
4. ALT sequence: `apply_snv_to_sequence` fetches the reference sequence from alphagenome-svc `/sequence`, validates that the base at `pos` matches `ref`, and substitutes `alt`. ALT pass: `_predict(None, alt_sequence, heads, …)`.
5. Per-head delta = `alt - ref`; compute L2 norm, max |Δ|, and index of the most-affected track (mean |Δ| over positions).
6. Emit `variant_delta` viz_spec.

**Output**:

```json
{
  "variant": "chr17:g.43044295A>G",
  "locus": "chr17:42978759-43109831",
  "deltas": [
    {"head": "atac",    "l2": 2.31, "max_abs_delta": 0.41, "top_track_index": 112},
    {"head": "chip_tf", "l2": 1.74, "max_abs_delta": 0.28, "top_track_index": 884}
  ],
  "viz_spec": {"type": "variant_delta", "head": "atac", "variant_position": 43044295, ...}
}
```

Both REF and ALT predictions are cached via `_predict`'s session cache; if the agent re-issues the same call, both forward passes are skipped.

---

### `upload_sequence`

Look up a sequence the user uploaded via `POST /upload`.

**Source**: [uploads.py](../services/agent_backend/tools/uploads.py)

**Inputs**:

| Name | Type | Meaning |
|---|---|---|
| `upload_id` | string | ID returned by `POST /upload` |

**Output**:

```json
{"upload_id": "uuid", "length": 2048, "source": "myseq.fasta", "sequence_preview": "ACGT…"}
```

This tool only returns metadata — it does NOT invoke AlphaGenome. To run prediction on an upload, enable `predict_tracks` in [config/tools.yaml](../config/tools.yaml) and chain the two (feasible only with stronger models).

---

## Primitives (disabled by default)

### `gene_to_locus`

**Source**: [genome_lookup.py](../services/agent_backend/tools/genome_lookup.py)

Ensembl REST gene → `{chrom, start, end, strand, biotype, ensembl_id, locus}`. 404 on unknown symbols returns a structured `{"error": "..."}` rather than raising.

### `predict_tracks`

**Source**: [alphagenome.py:22-106](../services/agent_backend/tools/alphagenome.py#L22-L106)

Low-level `/predict` wrapper.

- Takes `locus | sequence`, `heads`, `resolution`, `organism`.
- Decodes arrays, stashes them on `session.last_prediction`, returns shape summaries (mean, max, n_tracks per head) — NOT the full tensors. Tensors stay server-side for later retrieval via `GET /sessions/.../tracks`.

### `predict_variant_effect`

**Source**: [alphagenome.py:109-180](../services/agent_backend/tools/alphagenome.py#L109-L180)

Low-level version of `analyze_variant_effect`. Takes `locus, variant_position, ref, alt, heads, resolution, organism` — no HGVS parsing.

### `list_tracks_by_assay`

**Source**: [tracks.py](../services/agent_backend/tools/tracks.py)

Proxy to alphagenome-svc `/tracks` with `assay` / `tissue` / `head` / `organism` filters. Returns up to 50 rows.

### `get_top_tracks`

**Source**: [alphagenome.py:183-212](../services/agent_backend/tools/alphagenome.py#L183-L212)

Rank tracks from `session.last_prediction` by mean signal. Returns `{head, top_tracks: [{track_index, mean_signal}], prediction_id}`. Errors if there's no prior prediction.

### `render_panel`

**Source**: [viz.py](../services/agent_backend/tools/viz.py)

Emit a `viz_spec` without running anything. Takes `panel_type, head?, track_indices?`; uses the session's last prediction.

### `parse_hgvs`

**Source**: [variants.py](../services/agent_backend/tools/variants.py)

Regex-parse a simple genomic HGVS SNV. Returns `{chrom, position, ref, alt}` or an error.

---

## Planned stubs (neither class nor wiring exist)

These appear in [config/tools.yaml](../config/tools.yaml) as documentation of intent:

- `ensembl_regulatory_build(locus)` — annotated regulatory features.
- `jaspar_motif_scan(sequence, tf)` — motif sanity-check vs. a TF-binding prediction.
- `gtex_expression(gene, tissue)` — ground RNA-seq predictions in measured data.
- `conservation_score(locus)` — phyloP/phastCons via UCSC.
- `clinvar_lookup(locus_or_rsid)` — pathogenicity annotations.

To add one, follow [tutorials/05-adding-a-tool.md](tutorials/05-adding-a-tool.md).

---

## Side-effects on the session

Several tools mutate `session` in ways the rest of the system relies on:

- **`session.last_prediction`** — set by `predict_tracks`, `analyze_gene_tf_binding`, `analyze_region_regulation`, `analyze_variant_effect`. Holds the decoded numpy arrays. `GET /sessions/.../tracks` reads from here.
- **`session.pending_viz_spec`** — set by any macro that emits a visualization. Picked up and emitted as an SSE event by the next `step_callback` pass (see [agent.py:64-67](../services/agent_backend/agent.py#L64-L67)).
- **`session.predict_cache`** — populated lazily by `_predict`. Keyed on `(locus, sequence, heads, resolution, organism)`. Max 8 entries, FIFO eviction.
- **`session.uploads`** — populated by `POST /upload`, read by `upload_sequence`.

---

## Adding progress events to a custom tool

```python
from ._base import SessionAwareTool
from .macros import _emit

class MyTool(SessionAwareTool):
    ...
    def forward(self, ...):
        _emit(self.session_context, "progress", {"stage": "my_tool_start", "text": "..."})
        ...
        _emit(self.session_context, "progress", {"stage": "my_tool_done", "text": "done in 2.3s"})
```

The `_emit` helper no-ops if there's no bus attached (e.g. in tests or batch scripts), so the same tool works inside and outside a chat context.
