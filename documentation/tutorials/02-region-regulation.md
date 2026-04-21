# Tutorial 2 — Regulatory profile of a region

**Macro**: `analyze_region_regulation` ([source](../../services/agent_backend/tools/macros.py))
**Time**: ~15 s on CPU, one pass over three heads.

## Goal

Given a `chrN:start-end` locus, return the top tracks in three regulatory heads — chromatin accessibility (ATAC, DNase) and active histone marks (chip_histone) — and render them.

## Walkthrough

### Ask

> What regulatory elements does AlphaGenome predict at chr17:43044295-43175000?

### What happens

1. Router → `PREDICT`.
2. Agent picks `analyze_region_regulation(locus="chr17:43044295-43175000")`.
3. Macro runs ONE AlphaGenome forward pass with `heads=["atac","dnase","chip_histone"]` (default) — all three come out of a single pass, so this is not 3× slower than `analyze_gene_tf_binding`.
4. Per head, rank top 5 tracks by mean signal and annotate with `/tracks` metadata.
5. Emit `igv_tracks` viz_spec anchored on the first head (`atac`).
6. `final_answer`.

### UI

Right pane: one tab `igv_tracks · atac` with the top 5 ATAC tracks rendered in orange. The other heads' top tracks are in the tool observation but only the primary head's panel is opened — this is a single-tab design.

Typical summary:

> Top ATAC tracks (112, 44) show accessibility peaks at the 5′ end of the window; chip_histone track 309 (H3K27ac-class) marks the same region as active. DNase signal peaks at the same location. The IGV panel on the right renders the top 5 tracks per head.

## Variations

### Choose heads explicitly

> What H3K27ac do you see at chr19:44905781-44908683?

The agent should pass `heads=["chip_histone"]` and optionally `tissue_keywords=[...]` if tissue is mentioned.

### More tracks per head

> Top 15 ATAC tracks at chr7:5530000-5670000.

Agent should pass `top_k=15` and optionally restrict heads to `["atac"]`.

### Custom locus ranges

AlphaGenome expects exactly 131,072 bp of input. The macro doesn't resize — **this is a gap** — a user-supplied locus shorter than 131 kb will be rejected by alphagenome-svc with a 400. For flexible user ranges you need to pad/truncate to `max_input_length`; consider adding a helper that expands the input to match, centered on the midpoint of the requested locus.

Workaround today: supply a locus exactly `max_input_length` bp wide, or use `analyze_gene_tf_binding` which handles windowing for you.

## Output shape

```json
{
  "locus": "chr17:43044295-43175000",
  "heads": ["atac","dnase","chip_histone"],
  "top_tracks_by_head": {
    "atac":         [{"track_index": 112, "mean_signal": 1.3, "assay_title": "ATAC-seq", "biosample_name": "HepG2", ...}, ...],
    "dnase":        [...],
    "chip_histone": [...]
  },
  "notes": [],
  "viz_spec": {"type": "igv_tracks", "prediction_id": "...", "head": "atac", "track_indices": [112, ...]}
}
```

`notes` collects per-head warnings (e.g. "Track metadata unavailable" when the TSV isn't loaded).

## Session state after

Same pattern as `analyze_gene_tf_binding`:

- `session.last_prediction` holds three decoded arrays (`atac`, `dnase`, `chip_histone`), so subsequent `GET /sessions/.../tracks?head=dnase&...` calls can render other heads without re-running the model. This is how you'd build a "switch to DNase view" button in the UI — the arrays are already there.

## Direct invocation

```python
from services.agent_backend.tools.macros import AnalyzeRegionRegulation

tool = AnalyzeRegionRegulation(session_context=session)
r = tool.forward(locus="chr17:43044295-43175000", heads=["atac","chip_histone"], top_k=5)
for head, tracks in r["top_tracks_by_head"].items():
    print(head, [(t["track_index"], t["mean_signal"]) for t in tracks])
```
