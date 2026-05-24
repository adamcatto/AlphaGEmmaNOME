from __future__ import annotations

import re
from pathlib import Path

import pyfaidx

LOCUS_RE = re.compile(r"^(chr[\w]+):(\d+)-(\d+)$")


class LocusError(ValueError):
    pass


def parse_locus(locus: str) -> tuple[str, int, int]:
    m = LOCUS_RE.match(locus)
    if not m:
        raise LocusError(f"Bad locus format: {locus!r}. Expected 'chrN:start-end'.")
    chrom, start, end = m.group(1), int(m.group(2)), int(m.group(3))
    if end <= start:
        raise LocusError(f"Locus end must be > start: {locus!r}")
    return chrom, start, end


_fasta: pyfaidx.Fasta | None = None


def get_fasta(path: Path) -> pyfaidx.Fasta:
    global _fasta
    if _fasta is None:
        _fasta = pyfaidx.Fasta(str(path))
    return _fasta


def fetch_sequence(locus: str, fasta_path: Path) -> str:
    chrom, start, end = parse_locus(locus)
    fa = get_fasta(fasta_path)
    if chrom not in fa:
        raise LocusError(f"Chromosome {chrom!r} not in reference.")
    return str(fa[chrom][start:end]).upper()
