# Agent tools

Two tiers:

1. **Macros** (default, enabled in [config/tools.yaml](../config/tools.yaml)). Each one runs a full gene-lookup → AlphaGenome → rank → render pipeline so a small LLM only has to pick one tool per question.
2. **Primitives** (off by default). The chainable low-level tools; useful with stronger models for open-ended follow-ups. Toggle in `config/tools.yaml`.

Tool docstrings in `services/agent_backend/tools/*.py` are the source of truth.

---

## Macros

### `analyze_gene_tf_binding(gene_symbol, tissue_keywords=None, top_k=10, organism="human")`
Resolves the gene via Ensembl, runs AlphaGenome on a window centered at the TSS (width = `alphagenome.max_input_length`), ranks the top `chip_tf` tracks, filters by tissue keywords against track metadata (best-effort), and emits an `igv_tracks` viz_spec. Returns `{gene, window, top_tracks, note, viz_spec}`.

### `analyze_region_regulation(locus, heads=None, top_k=5, organism="human")`
Runs AlphaGenome on an explicit `chrN:start-end` locus across accessibility + histone heads (default `atac, dnase, chip_histone`), ranks top tracks per head, emits an `igv_tracks` viz_spec anchored to the first head.

### `analyze_variant_effect(variant_hgvs, heads=None, window_bp=None, organism="human")`
Parses an HGVS SNV (e.g. `chr17:g.43044295A>G`), runs AlphaGenome on ref + alt sequences over a window centered on the variant, returns per-head deltas (L2, top-affected track, max |Δ|), and emits a `variant_delta` viz_spec.

### `upload_sequence(upload_id)`
Reference a sequence the user previously uploaded via `POST /upload`.

---

## Primitives (disabled by default)

| Tool | Purpose |
|---|---|
| `gene_to_locus` | Ensembl HGNC symbol → coordinates |
| `predict_tracks` | Raw AlphaGenome call over locus/sequence |
| `predict_variant_effect` | Raw ref-vs-alt delta call |
| `list_tracks_by_assay` | Filter `track_metadata.tsv` by assay substring |
| `get_top_tracks` | Rank tracks from the last prediction |
| `render_panel` | Emit a viz_spec with no other side-effects |
| `parse_hgvs` | Parse genomic HGVS SNVs into components |

Re-enable any of these in `config/tools.yaml` if the model is reliable enough to chain them (Qwen 2.5 14B, Llama 3.1 8B+ typically handle this).

---

## Planned / stubbed

See [config/tools.yaml](../config/tools.yaml):

- `ensembl_regulatory_build` — annotated regulatory features for comparison.
- `jaspar_motif_scan` — sanity-check TF-binding predictions against known motifs.
- `gtex_expression` — ground `rnaseq` predictions in measured data.
- `conservation_score` — phyloP/phastCons via UCSC.
- `clinvar_lookup` — known pathogenicity.

---

## Track metadata

[services/alphagenome_svc/data/track_metadata.tsv](../services/alphagenome_svc/data/track_metadata.tsv) uses the DeepMind-published schema: `organism, output_type, name, strand, track_index, Assay title, File assembly, data_source, Target label, ontology_curie, biosample_name, biosample_type, gtex_tissue, gtex_tissue_group, …`. The svc maps `output_type` (uppercase) to our lowercase `head` values at load time.

Tissue matching in macros uses `biosample_name`, `gtex_tissue`, and `gtex_tissue_group`; TF/target annotation uses `target_label`.
