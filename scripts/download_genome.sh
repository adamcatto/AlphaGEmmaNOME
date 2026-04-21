#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FASTA="$(python3 "$ROOT/scripts/_paths.py" genome_fasta)"
FAI="$(python3 "$ROOT/scripts/_paths.py" genome_fai)"

mkdir -p "$(dirname "$FASTA")"

if [[ -f "$FASTA" ]]; then
  echo "hg38.fa already at $FASTA"
else
  echo "Downloading hg38.fa.gz from UCSC..."
  curl -L -o "$FASTA.gz" https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/hg38.fa.gz
  gunzip "$FASTA.gz"
fi

if [[ ! -f "$FAI" ]]; then
  echo "Indexing with samtools faidx..."
  if command -v samtools >/dev/null 2>&1; then
    samtools faidx "$FASTA"
  else
    python3 -c "import pyfaidx; pyfaidx.Fasta('$FASTA')"
  fi
fi

echo "Genome ready: $FASTA"
