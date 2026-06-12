"""ClinVar variant lookup via NCBI E-utilities (no auth required)."""

from __future__ import annotations

from typing import Any

from ._base import SessionAwareTool, http_client

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_EMAIL = "alphagemmanome@local"


class QueryClinvar(SessionAwareTool):
    name = "query_clinvar"
    description = (
        "Search ClinVar for variants associated with a gene. Returns clinical significance, "
        "condition, and review status for the top variants. Use when the user asks about "
        "pathogenic variants or disease associations for a specific gene."
    )
    inputs = {
        "gene_symbol": {"type": "string", "description": "HGNC gene symbol, e.g. 'BRCA1'."},
        "max_results": {"type": "integer", "description": "Maximum number of variants to return (default 20).", "nullable": True},
        "clinical_significance": {"type": "string", "description": "Filter by significance: 'pathogenic', 'likely_pathogenic', 'benign', etc. Optional.", "nullable": True},
    }
    output_type = "object"

    def forward(
        self,
        gene_symbol: str,
        max_results: int = 20,
        clinical_significance: str | None = None,
    ) -> dict[str, Any]:
        client = http_client()
        term = f"{gene_symbol}[GENE]"
        if clinical_significance:
            term += f" AND {clinical_significance}[CLNSIG]"

        search_r = client.get(
            f"{_EUTILS}/esearch.fcgi",
            params={"db": "clinvar", "term": term, "retmax": max_results, "retmode": "json", "email": _EMAIL},
            timeout=15,
        )
        if search_r.status_code != 200:
            return {"error": f"ClinVar esearch failed ({search_r.status_code})"}

        id_list = search_r.json().get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return {"gene_symbol": gene_symbol, "variants": [], "note": "No ClinVar entries found."}

        summary_r = client.get(
            f"{_EUTILS}/esummary.fcgi",
            params={"db": "clinvar", "id": ",".join(id_list), "retmode": "json", "email": _EMAIL},
            timeout=15,
        )
        if summary_r.status_code != 200:
            return {"error": f"ClinVar esummary failed ({summary_r.status_code})"}

        result_data = summary_r.json().get("result", {})
        variants: list[dict] = []
        for uid in result_data.get("uids", []):
            doc = result_data.get(uid, {})
            germline = doc.get("germline_classification", {})
            variants.append({
                "variation_id": uid,
                "title": doc.get("title", ""),
                "gene_symbol": gene_symbol,
                "clinical_significance": germline.get("description", doc.get("clinical_significance", {}).get("description", "")),
                "review_status": germline.get("review_status", ""),
                "condition": "; ".join(
                    t.get("trait_name", "") for t in doc.get("trait_set", []) if t.get("trait_name")
                ),
                "variant_type": doc.get("obj_type", ""),
                "last_evaluated": germline.get("last_evaluated", ""),
                "clinvar_url": f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{uid}/",
            })

        pathogenic = [v for v in variants if "pathogenic" in v["clinical_significance"].lower()]
        return {
            "gene_symbol": gene_symbol,
            "total_found": len(id_list),
            "variants": variants,
            "pathogenic_count": len(pathogenic),
            "summary": (
                f"Found {len(variants)} ClinVar entries for {gene_symbol}; "
                f"{len(pathogenic)} classified as pathogenic/likely pathogenic."
            ),
        }
