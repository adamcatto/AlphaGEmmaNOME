# Tutorial 3 — Variant effect (REF vs ALT)

**Macro**: `analyze_variant_effect` ([source](../../services/agent_backend/tools/macros.py))
**Time**: ~25–30 s on CPU — this macro runs AlphaGenome **twice** (reference sequence, then alternate).

## Goal

Given a single-base genomic variant (HGVS format: `chrN:g.POSREF>ALT`), compute the difference in AlphaGenome's predicted tracks between the reference and alternate sequences around the variant.

## Walkthrough

### Ask

> What is the effect of chr17:g.43044295A>G on chromatin accessibility?

### What happens

1. Router → `PREDICT`.
2. Agent picks `analyze_variant_effect(variant_hgvs="chr17:g.43044295A>G", heads=["atac"])`.
3. Macro regex-parses the HGVS: `chrom=chr17, pos=43044295, ref=A, alt=G`.
4. Build a 131-kb window centered on `pos`: `_window_around("chr17", 43044295, 131072)` → `chr17:42978759-43109831`.
5. **REF pass**: `_predict(locus="chr17:42978759-43109831", heads=["atac","chip_tf"], …)`. AlphaGenome fetches the reference sequence from the hg38 FASTA via alphagenome-svc.
6. **ALT sequence**: [`apply_snv_to_sequence`](../../services/agent_backend/_variant_utils.py):
   - `GET /sequence?locus=chr17:42978759-43109831` → the reference sequence.
   - Compute `offset = pos - start - 1 = 43044295 - 42978759 - 1 = 65535`.
   - Assert `sequence[65535] == "A"` (the declared ref base). If it doesn't match, raise `ValueError` — usually a coordinate-frame mistake.
   - Substitute `sequence[65535] = "G"`.
7. **ALT pass**: `_predict(sequence=<modified>, …)`.
8. For each head, compute `delta = alt - ref`; report L2 norm, max |Δ|, and the track index with the highest mean |Δ|.
9. Emit `variant_delta` viz_spec (currently falls through to JSON fallback in the UI — the variant-delta panel isn't implemented).

### Output

```json
{
  "variant": "chr17:g.43044295A>G",
  "locus": "chr17:42978759-43109831",
  "deltas": [
    {"head": "atac",    "l2": 2.31, "max_abs_delta": 0.41, "top_track_index": 112},
    {"head": "chip_tf", "l2": 1.74, "max_abs_delta": 0.28, "top_track_index": 884}
  ],
  "viz_spec": {"type": "variant_delta", "prediction_id": "...", "head": "atac", "variant_position": 43044295}
}
```

### Typical summary

> The A>G substitution produces an L2 delta of 2.3 on ATAC (max |Δ| 0.41 at track 112) and 1.7 on chip_tf (top-affected track 884). The variant-delta panel on the right visualizes the per-track change around the variant.

## Variations

### Multiple heads

> What's the effect of chr2:g.136114349G>T across expression and regulatory tracks?

Agent should pass `heads=["rna_seq","atac","chip_histone","chip_tf"]`.

### rsID (not supported)

The macro only accepts an HGVS string. For an rsID, you'd need a `clinvar_lookup` or `ensembl_vep` tool (both are planned stubs in [config/tools.yaml](../config/tools.yaml)) to resolve rsID → `{chrom, pos, ref, alt}` first.

### Custom window size

> Effect of chr17:g.43044295A>G over a 1 Mb window.

Agent passes `window_bp=1000000`. Note: AlphaGenome's max input is 131,072 (enforced in `limits.max_sequence_length`), so passing `window_bp=1000000` will be rejected with a 400 at predict time. Keep it ≤ 131,072.

## Performance notes

- Two forward passes. The per-session `predict_cache` in [macros.py](../../services/agent_backend/tools/macros.py) means the REF pass is cached by `(locus, None, heads, resolution, organism)` and the ALT pass by `(None, sequence, heads, resolution, organism)`. A retry of the same variant reuses both.
- If you then ask a follow-up like "what about H3K27ac at the same spot?", the REF cache hits but the ALT pass re-runs with the new head set (sequence is the same, but `heads` is in the cache key).

## Reference-base mismatch errors

If your variant call uses a different assembly or a different strand convention, `apply_snv_to_sequence` will raise:

```
ValueError: Reference mismatch at chr17:43044295: expected A, got G.
```

This becomes a `tool_call_result` observation with the error, and the agent typically surfaces it via `final_answer`. Check:

1. Assembly — the scaffold ships with GRCh38 / hg38. For GRCh37/hg19 variants you'd need a different FASTA.
2. Coordinate frame — HGVS genomic coordinates are 1-based. The macro computes `offset = pos - start - 1`, so an off-by-one in the user's input is the usual cause.
3. Strand — HGVS is always on the plus strand. If you got coordinates from a minus-strand transcript, convert to genomic.

## Direct invocation

```python
from services.agent_backend.tools.macros import AnalyzeVariantEffect

tool = AnalyzeVariantEffect(session_context=session)
r = tool.forward(variant_hgvs="chr17:g.43044295A>G", heads=["atac","chip_tf"])
for d in r["deltas"]:
    print(d["head"], "L2=", d["l2"], "top_track=", d["top_track_index"])
```

## What's NOT supported

- Insertions/deletions — only SNVs. The regex in [macros.py:437](../../services/agent_backend/tools/macros.py#L437) is `^(chr[\w]+):g\.(\d+)([ACGT])>([ACGT])$`.
- Compound variants / haplotypes — two variants at different positions on the same allele would require sequential edits to the sequence before the ALT pass. Not scaffolded.
- Structural variants — out of scope for a fixed 131-kb window.
