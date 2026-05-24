from __future__ import annotations

import base64
from typing import Any

import numpy as np

from schema import load_settings

from ._base import SessionAwareTool, http_client


def _svc_url() -> str:
    return load_settings().alphagenome.service_url


def _decode(arr: dict) -> np.ndarray:
    raw = base64.b64decode(arr["data_b64"])
    return np.frombuffer(raw, dtype=arr["dtype"]).reshape(arr["shape"])


class PredictTracks(SessionAwareTool):
    name = "predict_tracks"
    description = (
        "Predict AlphaGenome functional-genomics tracks (RNA-seq, TF ChIP, ATAC, etc.) "
        "for a genomic locus or a DNA sequence. Use when the user asks what a gene or "
        "region looks like functionally."
    )
    inputs = {
        "locus": {
            "type": "string",
            "description": "Genomic locus in 'chrN:start-end' form. Mutually exclusive with sequence.",
            "nullable": True,
        },
        "sequence": {
            "type": "string",
            "description": "Raw DNA sequence (A/C/G/T/N). Mutually exclusive with locus.",
            "nullable": True,
        },
        "heads": {
            "type": "array",
            "description": "AlphaGenome output heads to return. One or more of: atac, dnase, procap, cage, rna_seq, chip_tf, chip_histone, contact_maps, splice_sites, splice_junctions, splice_site_usage. Defaults from config if omitted.",
            "nullable": True,
        },
        "resolution": {
            "type": "string",
            "description": "'1bp' or '128bp'. Default '128bp'.",
            "nullable": True,
        },
        "organism": {
            "type": "string",
            "description": "'human' or 'mouse'. Default 'human'.",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(
        self,
        locus: str | None = None,
        sequence: str | None = None,
        heads: list[str] | None = None,
        resolution: str = "128bp",
        organism: str = "human",
    ) -> dict[str, Any]:
        heads = heads or load_settings().alphagenome.default_heads
        body = {
            "locus": locus,
            "sequence": sequence,
            "heads": heads,
            "resolution": resolution,
            "organism": organism,
        }
        r = http_client().post(f"{_svc_url()}/predict", json=body)
        r.raise_for_status()
        data = r.json()

        summaries = []
        decoded: dict[str, np.ndarray] = {}
        for arr_obj in data["arrays"]:
            arr = _decode(arr_obj)
            decoded[arr_obj["head"]] = arr
            summaries.append(
                {
                    "head": arr_obj["head"],
                    "shape": arr_obj["shape"],
                    "n_tracks": arr.shape[-1] if arr.ndim >= 1 else 0,
                    "mean": float(arr.mean()),
                    "max": float(arr.max()),
                }
            )

        if self.session_context is not None:
            p_data = {
                "prediction_id": data["prediction_id"],
                "locus": locus,
                "arrays": decoded,
                "resolution": resolution,
                "organism": organism,
            }
            self.session_context.last_prediction = p_data
            self.session_context.predictions[data["prediction_id"]] = p_data

        return {
            "prediction_id": data["prediction_id"],
            "locus": locus,
            "summaries": summaries,
        }


class PredictVariantEffect(SessionAwareTool):
    name = "predict_variant_effect"
    description = (
        "Predict the effect of a single nucleotide variant by running AlphaGenome twice "
        "(reference vs alternate) and returning per-track deltas. Use when the user asks "
        "about the functional impact of a variant."
    )
    inputs = {
        "locus": {"type": "string", "description": "Surrounding window 'chrN:start-end' containing the variant."},
        "variant_position": {
            "type": "integer",
            "description": "1-based genomic coordinate of the variant within the locus.",
        },
        "ref": {"type": "string", "description": "Reference base (A/C/G/T)."},
        "alt": {"type": "string", "description": "Alternate base (A/C/G/T)."},
        "heads": {"type": "array", "description": "Heads to compare."},
        "resolution": {"type": "string", "description": "'1bp' or '128bp'.", "nullable": True},
        "organism": {"type": "string", "description": "'human' or 'mouse'.", "nullable": True},
    }
    output_type = "object"

    def forward(
        self,
        locus: str,
        variant_position: int,
        ref: str,
        alt: str,
        heads: list[str],
        resolution: str = "128bp",
        organism: str = "human",
    ) -> dict[str, Any]:
        client = http_client()
        base_body = {"locus": locus, "heads": heads, "resolution": resolution, "organism": organism}

        r_ref = client.post(f"{_svc_url()}/predict", json=base_body)
        r_ref.raise_for_status()
        ref_pred = r_ref.json()

        from .._variant_utils import apply_snv_to_sequence

        alt_sequence = apply_snv_to_sequence(
            client, _svc_url(), locus, variant_position, ref, alt
        )
        alt_body = {
            "sequence": alt_sequence,
            "heads": heads,
            "resolution": resolution,
            "organism": organism,
        }
        r_alt = client.post(f"{_svc_url()}/predict", json=alt_body)
        r_alt.raise_for_status()
        alt_pred = r_alt.json()

        deltas = []
        for ref_arr_obj, alt_arr_obj in zip(ref_pred["arrays"], alt_pred["arrays"]):
            ref_arr = _decode(ref_arr_obj)
            alt_arr = _decode(alt_arr_obj)
            delta = alt_arr - ref_arr
            deltas.append(
                {
                    "head": ref_arr_obj["head"],
                    "l2": float(np.linalg.norm(delta)),
                    "top_track_index": int(np.argmax(np.abs(delta).mean(axis=tuple(range(delta.ndim - 1))))),
                    "max_abs_delta": float(np.max(np.abs(delta))),
                }
            )

        return {
            "locus": locus,
            "variant": f"{ref}>{alt}@{variant_position}",
            "deltas": deltas,
        }


class GetTopTracks(SessionAwareTool):
    name = "get_top_tracks"
    description = (
        "Given a previous predict_tracks result in this session, return the top-k tracks "
        "from a specified head ranked by mean signal over the window. Use to identify "
        "which assays/cell types dominate a region."
    )
    inputs = {
        "head": {"type": "string", "description": "Which AlphaGenome head to rank."},
        "k": {"type": "integer", "description": "Number of top tracks to return.", "nullable": True},
    }
    output_type = "object"

    def forward(self, head: str, k: int = 5) -> dict[str, Any]:
        if self.session_context is None or self.session_context.last_prediction is None:
            return {"error": "No prior prediction in this session — call predict_tracks first."}
        arr = self.session_context.last_prediction["arrays"].get(head)
        if arr is None:
            return {"error": f"Head {head!r} not in last prediction."}

        axes = tuple(range(arr.ndim - 1))
        means = arr.mean(axis=axes)
        top_idx = np.argsort(-means)[:k].tolist()
        return {
            "head": head,
            "top_tracks": [
                {"track_index": int(i), "mean_signal": float(means[i])} for i in top_idx
            ],
            "prediction_id": self.session_context.last_prediction["prediction_id"],
        }
