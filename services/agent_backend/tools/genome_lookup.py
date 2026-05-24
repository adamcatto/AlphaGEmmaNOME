from __future__ import annotations

from typing import Any

from schema import load_settings

from ._base import SessionAwareTool, http_client


class GeneToLocus(SessionAwareTool):
    name = "gene_to_locus"
    description = (
        "Look up the genomic coordinates of a gene by HGNC symbol using Ensembl REST. "
        "Use this BEFORE predict_tracks when the user mentions a gene by name."
    )
    inputs = {
        "gene_symbol": {"type": "string", "description": "HGNC gene symbol, e.g. 'BRCA1', 'TP53'."},
        "organism": {"type": "string", "description": "'human' or 'mouse'. Default 'human'.", "nullable": True},
    }
    output_type = "object"

    def forward(self, gene_symbol: str, organism: str = "human") -> dict[str, Any]:
        species = "human" if organism == "human" else "mouse"
        cfg = load_settings().ensembl
        url = f"{cfg.rest_base_url}/lookup/symbol/{species}/{gene_symbol}"
        r = http_client().get(url, headers={"Accept": "application/json"}, timeout=cfg.request_timeout_seconds)
        if r.status_code == 404:
            return {"error": f"Gene {gene_symbol!r} not found in Ensembl ({species})."}
        r.raise_for_status()
        data = r.json()
        return {
            "gene_symbol": gene_symbol,
            "ensembl_id": data.get("id"),
            "chrom": f"chr{data.get('seq_region_name')}",
            "start": data.get("start"),
            "end": data.get("end"),
            "strand": "+" if data.get("strand") == 1 else "-",
            "biotype": data.get("biotype"),
            "locus": f"chr{data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}",
        }
