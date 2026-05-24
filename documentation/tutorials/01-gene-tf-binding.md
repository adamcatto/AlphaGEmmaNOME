# Tutorial 1 — TF binding near a gene

**Macro**: `analyze_gene_tf_binding` ([source](../../services/agent_backend/tools/macros.py))
**Time**: ~15 s on CPU (first call cold-loads weights, +20 s).
**You need**: all four dev processes running (see [getting-started.md](../getting-started.md)).

## Goal

Ask a natural-language question about transcription-factor binding near a gene, optionally filtered by tissue, and get:

- A ranked list of AlphaGenome `chip_tf` tracks with their `target_label` (TF name) and sample context.
- A rendered track panel in the right pane.
- A 2–4 sentence summary in the chat.

## Walkthrough

### Step 1: ask the question

In the chat pane:

> Where do transcription factors bind near ASPM in the brain?

### Step 2: what the system does

1. **Router** classifies as `PREDICT` (mentions a gene, asks about binding).
2. **Agent** picks `analyze_gene_tf_binding(gene_symbol="ASPM", tissue_keywords=["brain","cortex","neural"], organism="human")` based on the system prompt's routing table and the in-context few-shot example for ASPM ([few_shot.json](../../services/agent_backend/prompts/few_shot.json)).
3. **Ensembl lookup**: GRCh38 coordinates for ASPM → `chr1:197084121-197146694`, strand `-`.
4. **Window**: midpoint = 197,115,407; window = `chr1:197049871-197180943` (131 kb, symmetric around midpoint — see [macros.py:300-301](../../services/agent_backend/tools/macros.py#L300-L301)).
5. **AlphaGenome**: one forward pass, `heads=["chip_tf"]`, `resolution="128bp"`. Takes 8–15 s on CPU.
6. **Rank**: top 30 tracks by mean signal over the window.
7. **Annotate**: fetch `GET /tracks?head=chip_tf&organism=human` from alphagenome-svc. Each track gets `target_label`, `biosample_name`, etc.
8. **Tissue filter**: keep only tracks where any of `biosample_name`, `gtex_tissue`, `gtex_tissue_group` contains `"brain"`, `"cortex"`, or `"neural"` (case-insensitive substring).
9. **Trim** to `top_k=10`.
10. **Emit** `igv_tracks` viz_spec → frontend opens a new tab.
11. **`final_answer`** with a 2–4 sentence summary.

### Step 3: what you see in the UI

Progression in the chat pane:

```
▶ thinking                    (collapsible; Qwen3's reasoning)
┃ ● Running AlphaGenome on chr1:197049871-197180943 for heads=['chip_tf']…
┃ ● AlphaGenome forward pass completed in 12.3s
● analyze_gene_tf_binding       done  ▶ (click to expand arguments/observation)

AlphaGenome predicts the strongest TF binding around ASPM (chr1 center ±65 kb)
at track 1204 (mean 0.74) and track 881 (mean 0.61); both are annotated
brain/neural ChIP samples. The IGV panel on the right shows the top 10
CTCF/NEUROG/REST-class tracks in that window.
```

In the right pane, a new tab opens: `igv_tracks · chip_tf`. Each row shows:

- **Label**: `target_label · biosample_name` (e.g. `CTCF · brain_cortex`).
- **Canvas plot**: filled area + outline, colored purple (chip_tf).
- **Hover**: vertical line + tooltip with value and fraction-of-window.

## Variations

### No tissue filter

> Where do TFs bind near BRCA1?

The macro runs without `tissue_keywords`, returning the top 10 tracks by signal regardless of sample. The summary should call out that you're looking at tracks across all tissues.

### Mouse gene

> Where do TFs bind near mouse Aspm?

The agent should pass `organism="mouse"`, which changes the Ensembl species and filters the track metadata to mouse rows. Note that most of AlphaGenome's `chip_tf` tracks are human (1,664) vs mouse — the mouse track count is significantly smaller.

### Custom top-k

> Show me the top 25 TF tracks near TP53.

The agent should pass `top_k=25`.

## Calling it directly (no agent)

If you want the macro output without going through the LLM, import and call it:

```python
from services.agent_backend.tools.macros import AnalyzeGeneTfBinding

class FakeSession:
    pending_viz_spec = None
    last_prediction = None
    predict_cache = None
    bus = None

tool = AnalyzeGeneTfBinding(session_context=FakeSession())
result = tool.forward(
    gene_symbol="ASPM",
    tissue_keywords=["brain","cortex","neural"],
    top_k=10,
    organism="human",
)
print(result["window"])                  # chr1:197049871-197180943
print(result["top_tracks"][0])           # {'track_index': 1204, 'mean_signal': 0.74, 'target_label': 'CTCF', ...}
print(result["viz_spec"])                # {'type': 'igv_tracks', ...}
```

This requires the agent-backend *and* alphagenome-svc to be running (the macro calls them over HTTP).

## Under the hood: what's stored server-side

After the macro returns, the session has:

- `session.last_prediction = {"prediction_id": ..., "locus": "chr1:197049871-197180943", "arrays": {"chip_tf": np.ndarray shape (1, 1024, 1664)}, ...}`. The raw tensor is kept so `GET /sessions/.../tracks` can return per-position data for the rendering step.
- `session.pending_viz_spec = {"type": "igv_tracks", ...}`. Surfaced as an SSE event and cleared.
- `session.predict_cache[("chr1:197049871-197180943", None, ("chip_tf",), "128bp", "human")] = <raw alphagenome response>`. If the agent retries with the same args, the cache hits instead of re-running AlphaGenome.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Tab opens but rows say `track 1204` with no TF name | Metadata TSV not loaded | `curl localhost:8001/tracks?head=chip_tf \| jq '.tracks \| length'` should be 1664. If 0, populate [track_metadata.tsv](../../services/alphagenome_svc/data/track_metadata.tsv). |
| All tracks have 0 mean signal | Coordinates are in a gap in the reference | Try a different gene; `N` characters in the FASTA produce zero predictions. |
| Tissue filter eliminated everything | Keywords don't match metadata | The macro falls back to unfiltered top tracks and adds a `note`. |
| Agent picks `final_answer` directly instead of the macro | Router classified as `ANSWER` | Rephrase to name a concrete gene and prediction intent ("predict", "AlphaGenome"). |
