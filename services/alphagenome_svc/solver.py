from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from schema import load_settings
from .model_loader import get_model
from .sequence import fetch_sequence, parse_locus

logger = logging.getLogger(__name__)

# Predefined consensus sequences for key transcription factor binding motifs
KNOWN_MOTIFS = {
    "CTCF": "CCGCCCCCTGGTGGCCT",
    "SP1": "GGGGCGGGGC",
    "AP-1": "TGACTCA",
    "TATA": "TATAAA",
    "OCT4": "ATGCAAAT",
    "NF-kB": "GGGACTTTCC"
}


def score_candidate(signal: float, mode: str, target: float | None, ref_signal: float) -> float:
    """Return a score where higher is better, based on the objective mode."""
    if mode == "maximize":
        return signal - ref_signal
    elif mode == "minimize":
        return ref_signal - signal
    elif mode == "target":
        if target is None:
            return 0.0
        return -abs(signal - target)
    return 0.0


def apply_edit(
    ref_seq: str,
    locus_chrom: str,
    locus_start: int,
    locus_end: int,
    edit_type: str,
    pos: int,  # 1-based genomic coordinate
    length: int,
    change_seq: str,  # for SNV/insertion/motif
    fasta_path: Path,
) -> str:
    """Apply an edit to the reference sequence and return a sequence of the exact same length."""
    seq_offset = pos - locus_start
    if edit_type == "snv":
        assert len(change_seq) == length
        return ref_seq[:seq_offset] + change_seq + ref_seq[seq_offset + length:]

    elif edit_type == "deletion":
        del_seq = ref_seq[:seq_offset] + ref_seq[seq_offset + length:]
        try:
            extra = fetch_sequence(f"{locus_chrom}:{locus_end}-{locus_end + length}", fasta_path)
        except Exception:
            extra = "T" * length  # safe fallback padding
        return del_seq + extra

    elif edit_type in ("insertion", "motif"):
        ins_seq = ref_seq[:seq_offset] + change_seq + ref_seq[seq_offset:]
        return ins_seq[:len(ref_seq)]

    return ref_seq


def solve_optimize_edits(
    locus: str,
    design_region_str: str,
    edit_type: str,
    objective_head: str,
    objective_track: int,
    objective_mode: str,
    target_value: float | None = None,
    max_edits: int = 1,
    organism: str = "human",
    top_k: int = 5,
    motif_name: str | None = None,
) -> dict[str, Any]:
    """Find the best genome edits using AlphaGenome predictions and state-space exploration."""
    settings = load_settings()
    model = get_model()
    if model is None:
        raise ValueError("AlphaGenome weights not loaded.")

    # 1. Fetch reference sequence and signal
    try:
        ref_seq = fetch_sequence(locus, settings.paths.genome_fasta)
    except FileNotFoundError:
        raise ValueError("Reference genome not available.")

    chrom, locus_start, locus_end = parse_locus(locus)
    des_chrom, des_start, des_end = parse_locus(design_region_str)
    organism_idx = settings.models.organism_index[organism]
    res_int = 128  # use 128bp for speed

    ref_preds = model.predict_sequence(ref_seq, organism_idx, (objective_head,), (res_int,))
    ref_signal = model.extract_track_signal(ref_preds, objective_head, objective_track, res_int)

    candidates: list[dict[str, Any]] = []

    # Helper to evaluate and store a mutated sequence
    def evaluate_sequence(
        mut_seq: str,
        m_type: str,
        pos: int,
        length: int,
        seq_change: str
    ):
        preds = model.predict_sequence(mut_seq, organism_idx, (objective_head,), (res_int,))
        signal = model.extract_track_signal(preds, objective_head, objective_track, res_int)
        delta = signal - ref_signal
        pct = (delta / max(abs(ref_signal), 1e-9)) * 100.0
        score = score_candidate(signal, objective_mode, target_value, ref_signal)

        candidates.append({
            "mutation_type": m_type,
            "position": pos,
            "length": length,
            "sequence_change": seq_change,
            "predicted_signal": round(signal, 6),
            "percent_change": round(pct, 2),
            "score": score,
            "mut_seq": mut_seq,
        })

    # 2. RUN ALGORITHMS BASED ON EDIT TYPE
    if edit_type == "snv":
        # Multi-site Beam Search / Single substitution scan
        # First, find single substitutions
        single_muts: list[dict[str, Any]] = []
        for genomic_pos in range(des_start, des_end):
            seq_offset = genomic_pos - locus_start
            if seq_offset < 0 or seq_offset >= len(ref_seq):
                continue
            ref_base = ref_seq[seq_offset].upper()
            for alt in "ACGT":
                if alt == ref_base:
                    continue
                mut_seq = apply_edit(
                    ref_seq, chrom, locus_start, locus_end, "snv",
                    genomic_pos, 1, alt, settings.paths.genome_fasta
                )
                preds = model.predict_sequence(mut_seq, organism_idx, (objective_head,), (res_int,))
                signal = model.extract_track_signal(preds, objective_head, objective_track, res_int)
                score = score_candidate(signal, objective_mode, target_value, ref_signal)
                single_muts.append({
                    "pos": genomic_pos,
                    "ref": ref_base,
                    "alt": alt,
                    "score": score,
                    "seq": mut_seq
                })

        # Sort single mutations by objective score descending
        single_muts.sort(key=lambda m: -m["score"])

        # If max_edits = 1, just keep top single mutations
        if max_edits == 1:
            for m in single_muts[:top_k]:
                evaluate_sequence(m["seq"], "snv", m["pos"], 1, f"{m['ref']}>{m['alt']}")
        else:
            # Beam search on top single mutations to find combinations
            # Top N mutations to combine
            pool = single_muts[:10]  # combine from the top 10 single mutations to be extremely fast and effective
            beam = [([m], m["score"], m["seq"]) for m in pool]

            for step in range(2, max_edits + 1):
                next_beam = []
                for active_muts, prev_score, active_seq in beam:
                    last_pos = active_muts[-1]["pos"]
                    for m in pool:
                        if m["pos"] <= last_pos:
                            continue  # avoid duplicate combinations and same positions
                        # apply mutation to active_seq
                        new_seq = apply_edit(
                            active_seq, chrom, locus_start, locus_end, "snv",
                            m["pos"], 1, m["alt"], settings.paths.genome_fasta
                        )
                        preds = model.predict_sequence(new_seq, organism_idx, (objective_head,), (res_int,))
                        signal = model.extract_track_signal(preds, objective_head, objective_track, res_int)
                        score = score_candidate(signal, objective_mode, target_value, ref_signal)

                        next_beam.append((active_muts + [m], score, new_seq))

                if not next_beam:
                    break
                next_beam.sort(key=lambda b: -b[1])
                beam = next_beam[:3]  # keep beam width of 3 for fast runtimes

            # Evaluate top beam candidates
            all_combos = []
            for muts, score, seq in beam:
                all_combos.append((muts, seq))
            # Also keep single ones as options
            for m in pool[:3]:
                all_combos.append(([m], m["seq"]))

            # Evaluate everything fully to candidates list
            seen_combos = set()
            for muts, seq in all_combos:
                combo_str = ", ".join(f"{m['ref']}>{m['alt']}@{m['pos']}" for m in muts)
                if combo_str in seen_combos:
                    continue
                seen_combos.add(combo_str)
                evaluate_sequence(seq, "snv", muts[0]["pos"], len(muts), combo_str)

    elif edit_type == "deletion":
        # Sliding-Window Deletion Scanner
        deletion_sizes = [5, 10, 25, 50]
        for L in deletion_sizes:
            if L > (des_end - des_start):
                continue
            stride = max(1, L // 5)
            for p in range(des_start, des_end - L + 1, stride):
                mut_seq = apply_edit(
                    ref_seq, chrom, locus_start, locus_end, "deletion",
                    p, L, "", settings.paths.genome_fasta
                )
                evaluate_sequence(mut_seq, "deletion", p, L, f"del {L}bp")

    elif edit_type == "insertion" or edit_type == "motif":
        # Determine motif or sequence to insert
        motif_seq = None
        if motif_name:
            motif_seq = KNOWN_MOTIFS.get(motif_name.upper())

        # If no motif matched but a motif_name was given, use it as a literal insertion sequence if it is valid DNA
        if not motif_seq and motif_name and all(c in "ACGTN" for c in motif_name.upper()):
            motif_seq = motif_name.upper()

        if not motif_seq:
            # default to CTCF as a strong regulator
            motif_seq = KNOWN_MOTIFS["CTCF"]
            motif_name = "CTCF"

        motif_len = len(motif_seq)

        # Decide whether to do Ablation or Insertion
        # If edit_type is "motif" and the motif already exists in the design region, ablate it.
        # Otherwise, perform insertion at different spacings.
        ablate_candidates: list[tuple[int, int]] = []
        des_seq = ref_seq[des_start - locus_start : des_end - locus_start].upper()

        # Find exact matches or single-mismatch matches of motif_seq in design region
        # Let's search for exact consensus matching
        for i in range(len(des_seq) - motif_len + 1):
            sub = des_seq[i : i + motif_len]
            # calculate mismatches
            mismatches = sum(1 for c1, c2 in zip(sub, motif_seq) if c1 != c2 and c2 != "N")
            if mismatches <= 2:  # allow up to 2 mismatches to find motifs
                ablate_candidates.append((des_start + i, motif_len))

        if edit_type == "motif" and ablate_candidates:
            # ABLATION MODE: systemically mutate or delete existing motif sites
            for p, L in ablate_candidates:
                # 1. Mutate option: replace with all T's (scramble)
                scrambled = "T" * L
                mut_seq = apply_edit(
                    ref_seq, chrom, locus_start, locus_end, "snv",
                    p, L, scrambled, settings.paths.genome_fasta
                )
                evaluate_sequence(mut_seq, "motif", p, L, f"ablate motif {motif_name} ({ref_seq[p-locus_start:p-locus_start+L]}>{scrambled})")
                
                # 2. Deletion option: delete it
                del_seq = apply_edit(
                    ref_seq, chrom, locus_start, locus_end, "deletion",
                    p, L, "", settings.paths.genome_fasta
                )
                evaluate_sequence(del_seq, "motif", p, L, f"delete motif {motif_name} ({L}bp)")
        else:
            # INSERTION MODE: systematically insert at various spacings/positions across design region
            stride = max(1, (des_end - des_start) // 10)  # up to 10 insertion positions
            for p in range(des_start, des_end + 1, stride):
                mut_seq = apply_edit(
                    ref_seq, chrom, locus_start, locus_end, "insertion",
                    p, motif_len, motif_seq, settings.paths.genome_fasta
                )
                evaluate_sequence(mut_seq, "insertion", p, motif_len, f"insert motif {motif_name} ({motif_seq})")

    # 3. Sort candidates by objective score descending
    candidates.sort(key=lambda c: -c["score"])
    top_candidates = candidates[:top_k]

    best = top_candidates[0] if top_candidates else None
    ref_seq_out = ref_seq
    edited_seq_out = best.get("mut_seq") if best else None

    # Clean up fields we don't serialize (like "score" and "mut_seq")
    for c in top_candidates:
        c.pop("score", None)
        c.pop("mut_seq", None)

    best = top_candidates[0] if top_candidates else None
    if best:
        sgn = "+" if best["percent_change"] >= 0 else ""
        summary = (
            f"Top edit: {best['sequence_change']} at {best['position']} "
            f"(predicted signal={best['predicted_signal']:.4f}, {sgn}{best['percent_change']:.1f}%)."
        )
    else:
        summary = f"No candidates found in {design_region_str}."

    return {
        "locus": locus,
        "design_region": design_region_str,
        "objective_head": objective_head,
        "objective_track": objective_track,
        "reference_signal": round(ref_signal, 6),
        "candidates": top_candidates,
        "summary": summary,
        "ref_seq": ref_seq_out,
        "edited_seq": edited_seq_out,
    }
