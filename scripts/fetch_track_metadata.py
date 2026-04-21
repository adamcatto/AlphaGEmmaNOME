"""Replace the placeholder track_metadata.tsv with the real DeepMind AlphaGenome
track annotations.

TODO: DeepMind published per-track metadata (assay, biosample ontology, target
gene/TF, etc.) alongside the JAX release. Locate the canonical table (likely a
CSV or GCS bucket referenced in the Borzoi/AlphaGenome paper supplements) and
normalize it to the columns used by the TSV in this repo:

    head, track_index, assay, cell_type, organism, source_sample_id

Row counts must match the model's output dimensions:

    atac=256, dnase=384, procap=128, cage=640, rnaseq=768,
    chip_tf=1664, chip_histone=1152, contact_maps=28,
    splice_sites=5, splice_junctions=734, splice_site_usage=734

Until this script is filled in, the UI should display a "metadata unavailable"
banner and the agent's list_tracks_by_assay tool will return TODO placeholder
rows.
"""
from __future__ import annotations

import sys

MESSAGE = (
    "fetch_track_metadata.py is not yet implemented — see the module docstring "
    "for the columns and row counts required. The placeholder TSV shipped in "
    "services/alphagenome_svc/data/track_metadata.tsv will remain in use until "
    "this script populates it from DeepMind's published annotation table."
)


def main() -> int:
    print(MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
