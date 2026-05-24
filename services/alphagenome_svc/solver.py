from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from schema import load_settings
from .model_loader import get_model
from .sequence import fetch_sequence, parse_locus

logger = logging.getLogger(__name__)

T = TypeVar("T")

ProgressCallback = Callable[[int, int, str], None]

# Predefined consensus sequences for key transcription factor binding motifs
KNOWN_MOTIFS = {
    "CTCF": "CCGCCCCCTGGTGGCCT",
    "SP1": "GGGGCGGGGC",
    "AP-1": "TGACTCA",
    "TATA": "TATAAA",
    "OCT4": "ATGCAAAT",
    "NF-kB": "GGGACTTTCC"
}


def evenly_sample(items: list[T], max_n: int | None) -> list[T]:
    """Return up to *max_n* items evenly spaced through *items* (preserving order)."""
    if max_n is None or len(items) <= max_n:
        return items
    if max_n <= 0:
        return []
    if max_n == 1:
        return [items[0]]

    n = len(items)
    indices = [round(i * (n - 1) / (max_n - 1)) for i in range(max_n)]
    seen: set[int] = set()
    result: list[T] = []
    for idx in indices:
        if idx not in seen:
            seen.add(idx)
            result.append(items[idx])
    return result


def evenly_spaced_positions(
    region_start: int,
    region_end: int,
    count: int,
    *,
    inset: int = 0,
) -> list[int]:
    """Return up to *count* genomic positions evenly spaced across [region_start, region_end - inset]."""
    if count <= 0:
        return []

    last_pos = region_end - inset
    if last_pos < region_start:
        return [region_start]

    if count == 1:
        return [region_start + (last_pos - region_start) // 2]

    span = last_pos - region_start
    seen: set[int] = set()
    positions: list[int] = []
    for i in range(count):
        pos = region_start + round(i * span / (count - 1))
        if pos not in seen:
            seen.add(pos)
            positions.append(pos)
    return positions


# Default deletion length when max_candidates requests evenly-spaced placement
_EVENLY_SPACED_DELETION_BP = 10


def _track_mean_detail(description: str, signal: float) -> str:
    """Format progress text with the objective track's mean signal across bins."""
    return f"{description} | track mean={signal:.6f}"


def _report_progress(
    current: int,
    total: int,
    detail: str,
    t_start: float,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Log and optionally emit a 1..N progress update."""
    if total <= 0:
        return

    percent = int((current / total) * 100)
    bar_len = 25
    filled = int(bar_len * current // total)
    if current >= total:
        bar = "=" * bar_len
    elif filled >= bar_len:
        bar = "=" * bar_len
    else:
        bar = "=" * filled + ">" + " " * (bar_len - filled - 1)

    elapsed = time.time() - t_start
    avg_time = elapsed / current if current > 0 else 0.0
    eta = avg_time * (total - current)
    eta_str = f"{int(eta)}s" if current > 0 else "--s"

    logger.info(
        f"[{bar}] {percent}% ({current}/{total}) | {detail} | "
        f"Elapsed: {elapsed:.1f}s | ETA: {eta_str}"
    )
    if progress_callback is not None:
        progress_callback(current, total, detail)


def score_candidate(signal: float, mode: str, target: float | None, ref_signal: float) -> float:
    """Return a score where higher is better, based on the log2 fold-change (LFC) over reference baseline."""
    import math
    eps = 1e-4
    lfc = math.log2((signal + eps) / (ref_signal + eps))

    if mode == "maximize":
        return lfc
    elif mode == "minimize":
        return -lfc
    elif mode == "target":
        if target is None:
            return 0.0
        target_lfc = math.log2((target + eps) / (ref_signal + eps))
        return -abs(lfc - target_lfc)
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
        # In-place replacement: keep sequence length and coordinate alignment so
        # each bin still maps to the same genomic position in the input window.
        pad_start = pos + length
        pad_end = pad_start + length
        try:
            fill = fetch_sequence(f"{locus_chrom}:{pad_start}-{pad_end}", fasta_path)
        except Exception:
            fill = "N" * length
        if len(fill) < length:
            fill = (fill + "N" * length)[:length]
        return ref_seq[:seq_offset] + fill + ref_seq[seq_offset + length:]

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
    max_candidates: int | None = None,
    progress_callback: ProgressCallback | None = None,
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
    logger.info(
        f"Reference {objective_head} track {objective_track} mean signal={ref_signal:.4f} "
        f"(avg across the 3 center bins at {res_int}bp resolution)"
    )

    candidates: list[dict[str, Any]] = []

    # Helper to evaluate and store a mutated sequence
    def evaluate_sequence(
        mut_seq: str,
        m_type: str,
        pos: int,
        length: int,
        seq_change: str,
        current_idx: int | None = None,
        total_count: int | None = None,
        t_start: float | None = None,
    ) -> float:
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

        if current_idx is not None and total_count is not None and t_start is not None:
            _report_progress(
                current_idx,
                total_count,
                _track_mean_detail(f"Evaluating: {seq_change} at pos {pos}", signal),
                t_start,
                progress_callback,
            )
        return score

    # 2. RUN ALGORITHMS BASED ON EDIT TYPE
    if edit_type == "snv":
        # Multi-site Beam Search / Single substitution scan
        # First, find single substitutions
        single_mut_specs = []
        for genomic_pos in range(des_start, des_end):
            seq_offset = genomic_pos - locus_start
            if seq_offset < 0 or seq_offset >= len(ref_seq):
                continue
            ref_base = ref_seq[seq_offset].upper()
            for alt in "ACGT":
                if alt == ref_base:
                    continue
                single_mut_specs.append((genomic_pos, ref_base, alt))

        full_single_count = len(single_mut_specs)
        single_mut_specs = evenly_sample(single_mut_specs, max_candidates)
        total_single = len(single_mut_specs)
        if max_candidates is not None and total_single < full_single_count:
            logger.info(
                f"SNV scan limited to {total_single}/{full_single_count} evenly-spaced candidates "
                f"(max_candidates={max_candidates})."
            )
        logger.info(f"Starting single substitution SNV scan: {total_single} candidates to evaluate.")

        single_muts: list[dict[str, Any]] = []
        t_start = time.time()
        for i, (genomic_pos, ref_base, alt) in enumerate(single_mut_specs, 1):
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

            _report_progress(
                i,
                total_single,
                _track_mean_detail(
                    f"Scanning base: {ref_base}>{alt} at pos {genomic_pos}", signal
                ),
                t_start,
                progress_callback,
            )

        # Sort single mutations by objective score descending
        single_muts.sort(key=lambda m: -m["score"])

        # If max_edits = 1, just keep top single mutations
        if max_edits == 1:
            for m in single_muts[:top_k]:
                evaluate_sequence(m["seq"], "snv", m["pos"], 1, f"{m['ref']}>{m['alt']}")
        else:
            # Beam search on top single mutations to find combinations
            pool = single_muts[:10]  # combine from the top 10 single mutations
            beam = [([m], m["score"], m["seq"]) for m in pool]

            for step in range(2, max_edits + 1):
                next_beam = []
                total_beam_evals = len(beam) * len(pool)
                logger.info(f"Beam search step {step}/{max_edits}: evaluating up to {total_beam_evals} combinations.")
                t_beam_start = time.time()
                beam_idx = 0

                for active_muts, prev_score, active_seq in beam:
                    last_pos = active_muts[-1]["pos"]
                    for m in pool:
                        beam_idx += 1
                        if m["pos"] <= last_pos:
                            continue
                        # apply mutation to active_seq
                        new_seq = apply_edit(
                            active_seq, chrom, locus_start, locus_end, "snv",
                            m["pos"], 1, m["alt"], settings.paths.genome_fasta
                        )
                        preds = model.predict_sequence(new_seq, organism_idx, (objective_head,), (res_int,))
                        signal = model.extract_track_signal(preds, objective_head, objective_track, res_int)
                        score = score_candidate(signal, objective_mode, target_value, ref_signal)

                        next_beam.append((active_muts + [m], score, new_seq))

                        _report_progress(
                            beam_idx,
                            total_beam_evals,
                            _track_mean_detail(
                                f"Beam step {step} | combo pos {m['pos']}", signal
                            ),
                            t_beam_start,
                            progress_callback,
                        )

                if not next_beam:
                    break
                next_beam.sort(key=lambda b: -b[1])
                beam = next_beam[:3]  # keep beam width of 3

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
        deletion_sizes = [5, 10, 25, 50]
        specs: list[tuple[str, str, int, int, str]] = []

        if max_candidates is not None:
            # Place exactly max_candidates deletions evenly across the full input locus
            L = _EVENLY_SPACED_DELETION_BP
            positions = evenly_spaced_positions(
                locus_start, locus_end, max_candidates, inset=2 * L
            )
            for p in positions:
                mut_seq = apply_edit(
                    ref_seq, chrom, locus_start, locus_end, "deletion",
                    p, L, "", settings.paths.genome_fasta
                )
                specs.append((mut_seq, "deletion", p, L, f"del {L}bp"))
            logger.info(
                f"Placing {len(specs)} x {L}bp deletions evenly across input locus "
                f"{chrom}:{locus_start}-{locus_end} (max_candidates={max_candidates})."
            )
        else:
            # Exhaustive sliding-window scan within design_region
            for L in deletion_sizes:
                if L > (des_end - des_start):
                    continue
                stride = max(1, L // 5)
                for p in range(des_start, des_end - L + 1, stride):
                    mut_seq = apply_edit(
                        ref_seq, chrom, locus_start, locus_end, "deletion",
                        p, L, "", settings.paths.genome_fasta
                    )
                    specs.append((mut_seq, "deletion", p, L, f"del {L}bp"))
            logger.info(
                f"Starting sliding-window deletion scan in design region: "
                f"{len(specs)} candidates to evaluate."
            )

        total = len(specs)
        t_start = time.time()
        for i, (m_seq, m_type, p, L, desc) in enumerate(specs, 1):
            evaluate_sequence(m_seq, m_type, p, L, desc, i, total, t_start)

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
        ablate_candidates: list[tuple[int, int]] = []
        des_seq = ref_seq[des_start - locus_start : des_end - locus_start].upper()

        # Find exact matches or single-mismatch matches of motif_seq in design region
        for i in range(len(des_seq) - motif_len + 1):
            sub = des_seq[i : i + motif_len]
            mismatches = sum(1 for c1, c2 in zip(sub, motif_seq) if c1 != c2 and c2 != "N")
            if mismatches <= 2:  # allow up to 2 mismatches to find motifs
                ablate_candidates.append((des_start + i, motif_len))

        specs = []
        if edit_type == "motif" and ablate_candidates:
            # ABLATION MODE: systemically mutate or delete existing motif sites
            for p, L in ablate_candidates:
                # 1. Mutate option: replace with all T's (scramble)
                scrambled = "T" * L
                mut_seq = apply_edit(
                    ref_seq, chrom, locus_start, locus_end, "snv",
                    p, L, scrambled, settings.paths.genome_fasta
                )
                specs.append((mut_seq, "motif", p, L, f"ablate motif {motif_name} ({ref_seq[p-locus_start:p-locus_start+L]}>{scrambled})"))

                # 2. Deletion option: delete it
                del_seq = apply_edit(
                    ref_seq, chrom, locus_start, locus_end, "deletion",
                    p, L, "", settings.paths.genome_fasta
                )
                specs.append((del_seq, "motif", p, L, f"delete motif {motif_name} ({L}bp)"))
        else:
            # INSERTION MODE: systematically insert at various spacings/positions across design region
            stride = max(1, (des_end - des_start) // 10)  # up to 10 insertion positions
            for p in range(des_start, des_end + 1, stride):
                mut_seq = apply_edit(
                    ref_seq, chrom, locus_start, locus_end, "insertion",
                    p, motif_len, motif_seq, settings.paths.genome_fasta
                )
                specs.append((mut_seq, "insertion", p, motif_len, f"insert motif {motif_name} ({motif_seq})"))

        full_motif_count = len(specs)
        specs = evenly_sample(specs, max_candidates)
        total = len(specs)
        if max_candidates is not None and total < full_motif_count:
            logger.info(
                f"Motif/insertion scan limited to {total}/{full_motif_count} evenly-spaced candidates "
                f"(max_candidates={max_candidates})."
            )
        logger.info(f"Starting motif/insertion scan: {total} candidates to evaluate.")
        t_start = time.time()
        for i, (m_seq, m_type, p, L, desc) in enumerate(specs, 1):
            evaluate_sequence(m_seq, m_type, p, L, desc, i, total, t_start)

    # 3. Sort candidates by objective score descending
    if objective_mode == "minimize":
        candidates.sort(key=lambda c: (-c["score"], c["predicted_signal"]))
    elif objective_mode == "maximize":
        candidates.sort(key=lambda c: (-c["score"], -c["predicted_signal"]))
    else:
        candidates.sort(key=lambda c: -c["score"])
    top_candidates = candidates[:top_k]

    best = top_candidates[0] if top_candidates else None
    ref_seq_out = ref_seq
    edited_seq_out = None
    if best is not None:
        edited_seq_out = best.get("mut_seq")
        if edited_seq_out is not None:
            edited_seq_out = str(edited_seq_out)
        logger.info(
            "Best edit: %s at pos %s (predicted_signal=%.6f, score=%.6f)",
            best.get("sequence_change"),
            best.get("position"),
            best.get("predicted_signal", 0.0),
            best.get("score", 0.0),
        )

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
