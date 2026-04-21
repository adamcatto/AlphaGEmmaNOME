"""GTEx gene expression data via GTEx Portal API v2 (no auth required)."""

from __future__ import annotations

from typing import Any

from ._base import SessionAwareTool, http_client

_GTEX_API = "https://gtexportal.org/api/v2"
_DEFAULT_DATASET = "gtex_v8"


class QueryGtexExpression(SessionAwareTool):
    name = "query_gtex_expression"
    description = (
        "Retrieve experimental gene expression data from GTEx across human tissues. "
        "Returns median TPM per tissue, top/bottom expressing tissues, and eQTL summary. "
        "Use when the user asks about expression patterns from actual RNA-seq experiments "
        "(as opposed to AlphaGenome predictions)."
    )
    inputs = {
        "gene_symbol": {"type": "string", "description": "HGNC gene symbol, e.g. 'TP53', 'BRCA1'."},
        "tissue_keywords": {"type": "array", "items": {"type": "string"}, "description": "Optional list of tissue substrings to filter (e.g. ['brain', 'liver']).", "nullable": True},
        "dataset_id": {"type": "string", "description": "GTEx dataset ID. Default 'gtex_v8'.", "nullable": True},
        "top_k": {"type": "integer", "description": "Number of highest/lowest expressing tissues to highlight (default 5).", "nullable": True},
    }
    output_type = "object"

    def forward(
        self,
        gene_symbol: str,
        tissue_keywords: list[str] | None = None,
        dataset_id: str = _DEFAULT_DATASET,
        top_k: int = 5,
    ) -> dict[str, Any]:
        client = http_client()

        expr_r = client.get(
            f"{_GTEX_API}/expression/geneExpression",
            params={"geneSymbol": gene_symbol, "datasetId": dataset_id},
            headers={"Accept": "application/json"},
            timeout=20,
        )

        if expr_r.status_code == 404:
            return {"error": f"Gene {gene_symbol!r} not found in GTEx ({dataset_id})."}
        if expr_r.status_code != 200:
            return {"error": f"GTEx API error ({expr_r.status_code}): {expr_r.text[:200]}"}

        data = expr_r.json().get("data", [])
        if not data:
            return {"gene_symbol": gene_symbol, "tissues": [], "note": "No GTEx expression data found."}

        tissues: list[dict] = []
        for entry in data:
            tissue_name = entry.get("tissueSiteDetailId", "").replace("_", " ")
            median_tpm = entry.get("median", 0.0)
            tissues.append({
                "tissue": tissue_name,
                "median_tpm": median_tpm,
                "n_samples": entry.get("numSamples", 0),
            })

        if tissue_keywords:
            kws = [k.lower() for k in tissue_keywords]
            filtered = [t for t in tissues if any(kw in t["tissue"].lower() for kw in kws)]
            if not filtered:
                filtered = tissues
                filter_note = f"No tissues matched keywords {tissue_keywords}; showing all tissues."
            else:
                filter_note = None
        else:
            filtered = tissues
            filter_note = None

        filtered_sorted = sorted(filtered, key=lambda x: -x["median_tpm"])
        high = filtered_sorted[:top_k]
        low = [t for t in reversed(filtered_sorted) if t["median_tpm"] > 0][:top_k]

        all_sorted = sorted(tissues, key=lambda x: -x["median_tpm"])
        global_max = all_sorted[0] if all_sorted else None

        eqtl_data = _fetch_eqtl_summary(client, gene_symbol, dataset_id)

        result: dict[str, Any] = {
            "gene_symbol": gene_symbol,
            "dataset": dataset_id,
            "total_tissues": len(tissues),
            "high_expressing_tissues": high,
            "low_expressing_tissues": low,
            "global_max_tissue": global_max,
            "eqtl_summary": eqtl_data,
            "summary": _summarize(gene_symbol, high, low, global_max),
        }
        if filter_note:
            result["filter_note"] = filter_note
        return result


def _fetch_eqtl_summary(client, gene_symbol: str, dataset_id: str) -> dict:
    try:
        r = client.get(
            f"{_GTEX_API}/association/singleTissueEqtl",
            params={"geneSymbol": gene_symbol, "datasetId": dataset_id, "numResults": 5},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if r.status_code == 200:
            eqtl_list = r.json().get("data", [])
            if eqtl_list:
                return {
                    "has_eqtls": True,
                    "top_eqtl_tissue": eqtl_list[0].get("tissueSiteDetailId", "").replace("_", " "),
                    "top_eqtl_slope": eqtl_list[0].get("slope"),
                    "top_eqtl_pvalue": eqtl_list[0].get("pValue"),
                }
    except Exception:
        pass
    return {"has_eqtls": None, "note": "eQTL data unavailable."}


def _summarize(
    gene: str,
    high: list[dict],
    low: list[dict],
    global_max: dict | None,
) -> str:
    if not high:
        return f"No expression data for {gene}."
    top_tissue = high[0]["tissue"]
    top_tpm = high[0]["median_tpm"]
    parts = [f"{gene} is most highly expressed in {top_tissue} (median {top_tpm:.1f} TPM)."]
    if len(high) > 1:
        others = ", ".join(t["tissue"] for t in high[1:3])
        parts.append(f"Also expressed in {others}.")
    if low and low[0]["median_tpm"] < top_tpm * 0.1:
        parts.append(f"Lowest expression detected in {low[0]['tissue']} ({low[0]['median_tpm']:.2f} TPM).")
    return " ".join(parts)
