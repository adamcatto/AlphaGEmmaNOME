from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.agent_backend.app import app as backend_app
from services.agent_backend.feedback_store import (
    DATA_DIR,
    DPO_FILE,
    SFT_FILE,
    export_dataset,
    get_stats,
    log_correction,
    log_preference,
)
from services.agent_backend.tools.genome_edits import OptimizeEdits
from services.alphagenome_svc.app import app as alpha_app
from services.alphagenome_svc.solver import evenly_sample, evenly_spaced_positions, score_candidate


# ---------------------------------------------------------------------------
# Part 1: Test feedback_store and REST Endpoints
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_feedback_files(tmp_path, monkeypatch):
    """Mocks feedback files to use a temporary directory for tests to avoid clutter."""
    temp_data_dir = tmp_path / "data"
    temp_data_dir.mkdir()
    monkeypatch.setattr("services.agent_backend.feedback_store.DATA_DIR", temp_data_dir)
    monkeypatch.setattr("services.agent_backend.feedback_store.DPO_FILE", temp_data_dir / "dpo_preferences.jsonl")
    monkeypatch.setattr("services.agent_backend.feedback_store.SFT_FILE", temp_data_dir / "sft_corrections.jsonl")


def test_feedback_logging_and_export():
    # 1. Log a preference pair (DPO) with tool calls
    log_preference(
        session_id="test-session-123",
        prompt="User: Silence track 0",
        chosen="Thoughts: let's delete... Answer: micro-deletion done",
        rejected="Answer: I cannot do that",
        chosen_tool_calls=[{"tool": "optimize_edits", "status": "done", "arguments": {"locus": "chr19:123"}}],
        rejected_tool_calls=[],
    )

    # 2. Log an expert correction (SFT)
    log_correction(
        session_id="test-session-123",
        prompt="User: Silence track 0",
        user_message="Silence track 0",
        original="Wrong thoughts",
        corrected="Corrected expert thoughts",
    )

    # 3. Check stats
    stats = get_stats()
    assert stats["dpo_count"] == 1
    assert stats["sft_count"] == 1

    # 4. Export datasets
    dpo_data = export_dataset("dpo")
    assert len(dpo_data) == 1
    assert dpo_data[0]["session_id"] == "test-session-123"
    assert dpo_data[0]["prompt"] == "User: Silence track 0"
    assert dpo_data[0]["chosen_tool_calls"] == [{"tool": "optimize_edits", "status": "done", "arguments": {"locus": "chr19:123"}}]
    assert dpo_data[0]["rejected_tool_calls"] == []

    sft_data = export_dataset("sft")
    assert len(sft_data) == 1
    assert sft_data[0]["corrected"] == "Corrected expert thoughts"


def test_backend_api_feedback_endpoints():
    client = TestClient(backend_app)

    # Test POST /feedback/preference
    res = client.post(
        "/feedback/preference",
        json={
            "session_id": "test-api-session",
            "prompt": "User: edit track 2",
            "chosen": "Correct plan",
            "rejected": "Wrong plan",
            "chosen_tool_calls": [{"tool": "optimize_edits", "status": "done"}],
            "rejected_tool_calls": [],
        },
    )
    assert res.status_code == 200
    assert res.json()["session_id"] == "test-api-session"
    assert res.json()["chosen_tool_calls"] == [{"tool": "optimize_edits", "status": "done"}]

    # Test POST /feedback/correction
    res = client.post(
        "/feedback/correction",
        json={
            "session_id": "test-api-session2",
            "prompt": "User: edit track 3",
            "user_message": "edit track 3",
            "original": "Faulty logic",
            "corrected": "Excellent expert logic",
        },
    )
    assert res.status_code == 200
    assert res.json()["corrected"] == "Excellent expert logic"

    # Test GET /feedback/stats
    res = client.get("/feedback/stats")
    assert res.status_code == 200
    assert res.json()["dpo_count"] == 1
    assert res.json()["sft_count"] == 1

    # Test GET /feedback/export
    res = client.get("/feedback/export?type=dpo")
    assert res.status_code == 200
    assert len(res.json()) == 1


# ---------------------------------------------------------------------------
# Part 2: Test AlphaGenome Solver helper metrics & Endpoint validation
# ---------------------------------------------------------------------------

def test_score_candidate():
    import math
    eps = 1e-4
    # Test maximize
    expected_max = math.log2((15.0 + eps) / (10.0 + eps))
    assert abs(score_candidate(15.0, "maximize", None, 10.0) - expected_max) < 1e-7

    # Test minimize
    expected_min = -math.log2((3.0 + eps) / (10.0 + eps))
    assert abs(score_candidate(3.0, "minimize", None, 10.0) - expected_min) < 1e-7

    # Test target value matching
    target_lfc = math.log2((10.0 + eps) / (10.0 + eps))
    lfc = math.log2((8.5 + eps) / (10.0 + eps))
    expected_target = -abs(lfc - target_lfc)
    assert abs(score_candidate(8.5, "target", 10.0, 10.0) - expected_target) < 1e-7


def test_evenly_sample():
    items = list(range(100))
    sampled = evenly_sample(items, 10)
    assert len(sampled) == 10
    assert sampled[0] == 0
    assert sampled[-1] == 99
    assert evenly_sample(items, None) == items
    assert evenly_sample(items, 200) == items
    assert evenly_sample(items, 1) == [0]
    assert evenly_sample([], 5) == []


def test_evenly_sample_preserves_order():
    items = [f"pos_{i}" for i in range(50)]
    sampled = evenly_sample(items, 5)
    assert sampled == ["pos_0", "pos_12", "pos_24", "pos_37", "pos_49"]


def test_evenly_spaced_positions():
    pos = evenly_spaced_positions(1000, 2000, 5, inset=10)
    assert len(pos) == 5
    assert pos[0] == 1000
    assert pos[-1] == 1990

    one = evenly_spaced_positions(100, 200, 1, inset=0)
    assert one == [150]

    assert evenly_spaced_positions(100, 105, 10, inset=10) == [100]


def test_evenly_spaced_positions_deduplicates():
    # Very small span vs large count → dedupe collapses duplicates
    pos = evenly_spaced_positions(100, 110, 20, inset=0)
    assert len(pos) < 20
    assert pos[0] == 100
    assert pos[-1] == 110


def test_deletion_replaces_in_place_preserving_length():
    from unittest.mock import patch

    from services.alphagenome_svc.solver import apply_edit

    ref = "A" * 20
    locus_start = 1000
    locus_end = 1020
    pos = 1005
    length = 5

    with patch("services.alphagenome_svc.solver.fetch_sequence") as mock_fetch:
        mock_fetch.return_value = "TTTTT"
        result = apply_edit(
            ref,
            "chr1",
            locus_start,
            locus_end,
            "deletion",
            pos,
            length,
            "",
            fasta_path=None,  # type: ignore[arg-type]
        )

    assert len(result) == len(ref)
    assert result == "AAAAA" + "TTTTT" + "A" * 10
    assert result[10:] == ref[10:]  # 3' tail unchanged — coordinate alignment preserved
    mock_fetch.assert_called_once_with("chr1:1010-1015", None)


def test_resolve_subregion_relative_design_region():
    from services.alphagenome_svc.app import _resolve_subregion

    locus = "chr20:10174790-10305862"

    # LLM-appended ±N to gene end coordinate (the failing case)
    chrom, start, end = _resolve_subregion(locus, "chr20:10172395-10308258±100", half_default=50)
    assert chrom == "chr20"
    # Center clamped to locus_end because full-gene TSS exceeds clipped input window
    assert start == 10305762
    assert end == 10305862

    # Single coordinate ±N (inside locus)
    chrom, start, end = _resolve_subregion(locus, "chr20:10240326±100", half_default=50)
    assert start == 10240226
    assert end == 10240426

    # Half-width only → locus center
    chrom, start, end = _resolve_subregion(locus, "±100", half_default=50)
    center = (10174790 + 10305862) // 2
    assert start == center - 100
    assert end == center + 100

    # design_region_half_bp overrides default
    chrom, start, end = _resolve_subregion(locus, None, half_default=50, half_bp=100)
    center = (10174790 + 10305862) // 2
    assert start == center - 100
    assert end == center + 100


def test_alphagenome_svc_optimize_edits_validation():
    client = TestClient(alpha_app)

    # Mock the solver to bypass actual PyTorch execution if weights not loaded
    with patch("services.alphagenome_svc.solver.solve_optimize_edits") as mock_solve:
        mock_solve.return_value = {
            "locus": "chr17:43125200-43125600",
            "design_region": "chr17:43125200-43125600",
            "objective_head": "cage",
            "objective_track": 0,
            "reference_signal": 10.0,
            "candidates": [
                {
                    "mutation_type": "deletion",
                    "position": 43125300,
                    "length": 10,
                    "sequence_change": "del 10bp",
                    "predicted_signal": 2.0,
                    "percent_change": -80.0,
                }
            ],
            "summary": "Top edit: del 10bp (-80.0%)",
        }

        # Send request to FastAPI TestClient
        res = client.post(
            "/optimize_edits",
            json={
                "locus": "chr17:43125200-43125600",
                "edit_type": "deletion",
                "objective_head": "cage",
                "objective_track": 0,
                "objective_mode": "minimize",
                "max_edits": 1,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["objective_head"] == "cage"
        assert len(data["candidates"]) == 1
        assert data["candidates"][0]["mutation_type"] == "deletion"


# ---------------------------------------------------------------------------
# Part 3: Test OptimizeEdits Agent Tool instantiation
# ---------------------------------------------------------------------------

def test_optimize_edits_tool_declaration():
    tool = OptimizeEdits()
    assert tool.name == "optimize_edits"
    assert "objective_mode" in tool.inputs
    assert "edit_type" in tool.inputs
    assert "max_candidates" in tool.inputs


# ---------------------------------------------------------------------------
# Part 4: Test Locus adjustment (symmetrical contraction & expansion)
# ---------------------------------------------------------------------------

def test_adjust_locus_to_multiple():
    from services.alphagenome_svc.app import _adjust_locus_to_multiple, _parse_locus_coords

    # Test symmetrical expansion when too small
    small_locus = "chr19:44905791-44905891"  # 100 bp
    adjusted_small = _adjust_locus_to_multiple(small_locus)
    chrom, start, end = _parse_locus_coords(adjusted_small)
    length = end - start
    assert length >= 16384
    assert length % 2048 == 0

    # Test symmetrical contraction when too large
    # SNAP25 is ~135kb: chr20:10172395-10308258
    large_locus = "chr20:10172395-10308258"
    adjusted_large = _adjust_locus_to_multiple(large_locus)
    chrom_l, start_l, end_l = _parse_locus_coords(adjusted_large)
    length_l = end_l - start_l
    assert length_l == 131072  # EXACT max sequence length
    assert length_l % 2048 == 0


# ---------------------------------------------------------------------------
# Part 5: Test Intent Router Node and Classifier Routing
# ---------------------------------------------------------------------------

def test_intent_router_node():
    from services.agent_backend.nodes.intent_router import intent_router_node, route_by_intent
    from langchain_core.messages import HumanMessage, AIMessage

    # Mock State
    state = {
        "messages": [
            HumanMessage(content="Optimize expression of SNAP25"),
        ],
        "session_id": "test-session",
        "intent": None,
        "viz_specs": [],
        "step_count": 0,
    }

    # Test route by intent conditional edge
    state_predict = {"intent": "predict"}
    assert route_by_intent(state_predict) == "predict"

    state_answer = {"intent": "answer"}
    assert route_by_intent(state_answer) == "answer"

    # Mock the LLM inside intent_router_node to return a predicted content
    with patch("services.agent_backend.nodes.intent_router._build_llm") as mock_build:
        mock_llm = MagicMock()
        mock_build.return_value = mock_llm

        # 1. Test routing classifies to predict
        mock_llm.invoke.return_value = AIMessage(content="PREDICT")
        res = intent_router_node(state)
        assert res["intent"] == "predict"

        # 2. Test routing classifies to answer
        mock_llm.invoke.return_value = AIMessage(content="ANSWER")
        res = intent_router_node(state)
        assert res["intent"] == "answer"


def test_alphagenome_prediction_statelessness():
    from services.alphagenome_svc.solver import get_model, fetch_sequence, load_settings, apply_edit
    settings = load_settings()
    model = get_model()
    if model is None:
        return # Skip if model weights not loaded (e.g. in environments without weights)

    ref_seq = fetch_sequence("chr20:10174790-10305862", settings.paths.genome_fasta)
    pos1 = 10240198
    pos2 = 10240208

    alt1 = "A" if ref_seq[pos1 - 10174790].upper() != "A" else "C"
    mut_seq1 = apply_edit(ref_seq, "chr20", 10174790, 10305862, "snv", pos1, 1, alt1, settings.paths.genome_fasta)

    alt2 = "A" if ref_seq[pos2 - 10174790].upper() != "A" else "C"
    mut_seq2 = apply_edit(ref_seq, "chr20", 10174790, 10305862, "snv", pos2, 1, alt2, settings.paths.genome_fasta)

    # 1st prediction (Sequence 1)
    preds1_a = model.predict_sequence(mut_seq1, 0, ("rna_seq",), (128,))
    signal1_a = model.extract_track_signal(preds1_a, "rna_seq", 656, 128)

    # 2nd prediction (Sequence 2)
    preds2 = model.predict_sequence(mut_seq2, 0, ("rna_seq",), (128,))
    signal2_a = model.extract_track_signal(preds2, "rna_seq", 656, 128)

    # 3rd prediction (Sequence 1 again)
    preds1_b = model.predict_sequence(mut_seq1, 0, ("rna_seq",), (128,))
    signal1_b = model.extract_track_signal(preds1_b, "rna_seq", 656, 128)

    # Predictions for the same sequence must be identical and not decay!
    assert abs(signal1_a - signal1_b) < 1e-7



