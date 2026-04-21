"""GWAS Catalog trait associations via EBI REST API (no auth required)."""

from __future__ import annotations

from typing import Any

from ._base import SessionAwareTool, http_client

_GWAS_BASE = "https://www.ebi.ac.uk/gwas/rest/api"


class QueryGwasCatalog(SessionAwareTool):
    name = "query_gwas_catalog"
    description = (
        "Search the GWAS Catalog for trait associations linked to a gene. Returns "
        "traits, p-values, risk alleles, and publication details. Use when the user asks "
        "what diseases or phenotypes are associated with a gene or genomic region."
    )
    inputs = {
        "gene_symbol": {"type": "string", "description": "HGNC gene symbol, e.g. 'TCF7L2', 'APOE'."},
        "max_results": {"type": "integer", "description": "Max number of associations to return (default 25).", "nullable": True},
    }
    output_type = "object"

    def forward(self, gene_symbol: str, max_results: int = 25) -> dict[str, Any]:
        client = http_client()

        r = client.get(
            f"{_GWAS_BASE}/singleNucleotidePolymorphisms/search/findByGene",
            params={"geneName": gene_symbol, "size": 1},
            headers={"Accept": "application/json"},
            timeout=15,
        )

        # Prefer association-level search for richer data
        assoc_r = client.get(
            f"{_GWAS_BASE}/associations/search/findByGene",
            params={"geneName": gene_symbol, "size": max_results},
            headers={"Accept": "application/json"},
            timeout=20,
        )

        if assoc_r.status_code != 200:
            return {"error": f"GWAS Catalog API error ({assoc_r.status_code})"}

        embedded = assoc_r.json().get("_embedded", {})
        raw_assocs = embedded.get("associations", [])

        if not raw_assocs:
            return {"gene_symbol": gene_symbol, "associations": [], "note": "No GWAS Catalog associations found."}

        associations: list[dict] = []
        for a in raw_assocs:
            loci = a.get("loci", [{}])
            risk_alleles = []
            for locus in loci:
                for ra in locus.get("strongestRiskAlleles", []):
                    risk_alleles.append(ra.get("riskAlleleName", ""))

            study_links = a.get("_links", {}).get("study", {}).get("href", "")

            associations.append({
                "trait": "; ".join(
                    e.get("trait", "") for e in a.get("efoTraits", []) if e.get("trait")
                ) or "unknown",
                "p_value": a.get("pvalueMantissa", "") and a.get("pvalueExponent", "") and
                           f"{a.get('pvalueMantissa')}e{a.get('pvalueExponent')}",
                "risk_alleles": risk_alleles,
                "beta_or_or": a.get("betaNum"),
                "beta_unit": a.get("betaUnit"),
                "or_per_copy": a.get("orPerCopyNum"),
                "study_url": study_links.replace("{?projection}", "") if study_links else None,
            })

        trait_counts: dict[str, int] = {}
        for a in associations:
            t = a["trait"]
            trait_counts[t] = trait_counts.get(t, 0) + 1
        top_traits = sorted(trait_counts.items(), key=lambda x: -x[1])[:5]

        return {
            "gene_symbol": gene_symbol,
            "total_associations": len(associations),
            "associations": associations,
            "top_traits": [{"trait": t, "association_count": c} for t, c in top_traits],
            "summary": (
                f"Found {len(associations)} GWAS associations for {gene_symbol}. "
                f"Top trait: {top_traits[0][0] if top_traits else 'none'}."
            ),
        }
