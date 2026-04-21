"""gnomAD population genetics data via GraphQL (no auth required)."""

from __future__ import annotations

from typing import Any

from ._base import SessionAwareTool, http_client

_GNOMAD_API = "https://gnomad.broadinstitute.org/api"

_GENE_QUERY = """
query GeneConstraint($geneSymbol: String!, $referenceGenome: ReferenceGenomeId!) {
  gene(gene_symbol: $geneSymbol, reference_genome: $referenceGenome) {
    gene_id
    gene_symbol
    gnomad_constraint {
      exp_syn
      obs_syn
      syn_z
      exp_mis
      obs_mis
      mis_z
      exp_lof
      obs_lof
      pLI
      loeuf
      oe_lof
      oe_lof_lower
      oe_lof_upper
    }
    clinvar_variants {
      variant_id
      clinical_significance
      major_consequence
      pos
      ref
      alt
    }
  }
}
"""

_VARIANT_QUERY = """
query VariantFrequency($variantId: String!, $datasetId: DatasetId!) {
  variant(variant_id: $variantId, dataset: $datasetId) {
    variant_id
    consequence
    hgvsp
    hgvsc
    genome {
      ac
      an
      af
      populations {
        id
        ac
        an
        af
      }
    }
    clinvar {
      clinical_significance
      conflicting_pathogenicity_classifications
    }
  }
}
"""


class QueryGnomad(SessionAwareTool):
    name = "query_gnomad"
    description = (
        "Query gnomAD for gene-level constraint scores (pLI, LOEUF, Z-scores) and "
        "ClinVar-linked variants with population allele frequencies. Useful for assessing "
        "whether a gene is intolerant to loss-of-function or missense variation."
    )
    inputs = {
        "gene_symbol": {"type": "string", "description": "HGNC gene symbol, e.g. 'BRCA1'."},
        "reference_genome": {"type": "string", "description": "'GRCh38' or 'GRCh37'. Default 'GRCh38'.", "nullable": True},
        "include_clinvar_variants": {"type": "boolean", "description": "Whether to include ClinVar variants. Default true.", "nullable": True},
    }
    output_type = "object"

    def forward(
        self,
        gene_symbol: str,
        reference_genome: str = "GRCh38",
        include_clinvar_variants: bool = True,
    ) -> dict[str, Any]:
        r = http_client().post(
            _GNOMAD_API,
            json={"query": _GENE_QUERY, "variables": {"geneSymbol": gene_symbol, "referenceGenome": reference_genome}},
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
        if r.status_code != 200:
            return {"error": f"gnomAD API error ({r.status_code}): {r.text[:200]}"}

        data = r.json()
        if "errors" in data:
            return {"error": f"gnomAD query error: {data['errors'][0]['message']}"}

        gene = (data.get("data") or {}).get("gene")
        if not gene:
            return {"error": f"Gene {gene_symbol!r} not found in gnomAD ({reference_genome})."}

        constraint = gene.get("gnomad_constraint") or {}
        clinvar_variants = []
        if include_clinvar_variants:
            for v in (gene.get("clinvar_variants") or [])[:20]:
                clinvar_variants.append({
                    "variant_id": v.get("variant_id"),
                    "clinical_significance": v.get("clinical_significance"),
                    "consequence": v.get("major_consequence"),
                    "position": v.get("pos"),
                    "change": f"{v.get('ref')}>{ v.get('alt')}",
                })

        pLI = constraint.get("pLI")
        loeuf = constraint.get("loeuf")
        interpretation = _interpret_constraint(gene_symbol, pLI, loeuf, constraint.get("mis_z"))

        return {
            "gene_symbol": gene_symbol,
            "ensembl_id": gene.get("gene_id"),
            "reference_genome": reference_genome,
            "constraint": {
                "pLI": pLI,
                "loeuf": loeuf,
                "oe_lof": constraint.get("oe_lof"),
                "oe_lof_ci": f"{constraint.get('oe_lof_lower'):.2f}–{constraint.get('oe_lof_upper'):.2f}" if constraint.get("oe_lof_lower") is not None else None,
                "mis_z": constraint.get("mis_z"),
                "syn_z": constraint.get("syn_z"),
            },
            "clinvar_variants": clinvar_variants,
            "interpretation": interpretation,
        }


def _interpret_constraint(gene: str, pLI: float | None, loeuf: float | None, mis_z: float | None) -> str:
    parts: list[str] = []
    if pLI is not None:
        if pLI >= 0.9:
            parts.append(f"{gene} is highly intolerant to loss-of-function (pLI={pLI:.2f}), suggesting haploinsufficiency.")
        elif pLI >= 0.5:
            parts.append(f"{gene} shows moderate LoF intolerance (pLI={pLI:.2f}).")
        else:
            parts.append(f"{gene} is tolerant of loss-of-function variation (pLI={pLI:.2f}).")
    if loeuf is not None:
        if loeuf < 0.35:
            parts.append(f"LOEUF={loeuf:.2f} confirms strong constraint (top decile).")
        elif loeuf < 0.6:
            parts.append(f"LOEUF={loeuf:.2f} indicates moderate constraint.")
    if mis_z is not None and mis_z > 3.09:
        parts.append(f"High missense Z-score ({mis_z:.2f}) indicates intolerance to missense variation.")
    return " ".join(parts) if parts else "Constraint data available but interpretation inconclusive."
