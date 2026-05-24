from __future__ import annotations

import re
from typing import Any

from ._base import SessionAwareTool

_HGVS_GENOMIC = re.compile(r"^(NC_\d+\.\d+|chr[\w]+):g\.(\d+)([ACGT])>([ACGT])$")


class ParseHgvs(SessionAwareTool):
    name = "parse_hgvs"
    description = (
        "Parse a simple HGVS genomic variant string (e.g. 'chr17:g.43044295A>G') into "
        "chromosome, position, ref, alt components. Only supports single-base substitutions."
    )
    inputs = {
        "hgvs": {"type": "string", "description": "HGVS string like 'chr17:g.43044295A>G'."}
    }
    output_type = "object"

    def forward(self, hgvs: str) -> dict[str, Any]:
        m = _HGVS_GENOMIC.match(hgvs)
        if not m:
            return {"error": f"Could not parse {hgvs!r} as a simple genomic HGVS substitution."}
        chrom, pos, ref, alt = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        return {"chrom": chrom, "position": pos, "ref": ref, "alt": alt}
