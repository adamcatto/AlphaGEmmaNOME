from __future__ import annotations

import httpx


def apply_snv_to_sequence(
    client: httpx.Client,
    svc_url: str,
    locus: str,
    variant_position: int,
    ref: str,
    alt: str,
) -> str:
    """Fetch the reference sequence for a locus and return a copy with a single
    base substitution applied at `variant_position` (1-based). Raises if the
    reference base at that position doesn't match `ref`.
    """
    r = client.get(f"{svc_url}/sequence", params={"locus": locus})
    r.raise_for_status()
    sequence = r.json()["sequence"]

    chrom, start_end = locus.split(":")
    start = int(start_end.split("-")[0])
    offset = variant_position - start - 1
    if offset < 0 or offset >= len(sequence):
        raise ValueError(f"Variant position {variant_position} outside locus {locus}.")
    if sequence[offset].upper() != ref.upper():
        raise ValueError(
            f"Reference mismatch at {chrom}:{variant_position}: "
            f"expected {ref}, got {sequence[offset]}."
        )
    return sequence[:offset] + alt.upper() + sequence[offset + 1 :]
