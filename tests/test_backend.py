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
from services.alphagenome_svc.solver import score_candidate


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
    # 1. Log a preference pair (DPO)
    log_preference(
        session_id="test-session-123",
        prompt="User: Silence track 0",
        chosen="Thoughts: let's delete... Answer: micro-deletion done",
        rejected="Answer: I cannot do that",
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
        },
    )
    assert res.status_code == 200
    assert res.json()["session_id"] == "test-api-session"

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
    # Test maximize
    assert score_candidate(15.0, "maximize", None, 10.0) == 5.0
    # Test minimize
    assert score_candidate(3.0, "minimize", None, 10.0) == 7.0
    # Test target value matching
    assert score_candidate(8.5, "target", 10.0, 10.0) == -1.5


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
