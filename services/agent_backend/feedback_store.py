from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock
from typing import Any

DATA_DIR = Path("/opt/software/OmniGemmaNome/services/agent_backend/data")
DPO_FILE = DATA_DIR / "dpo_preferences.jsonl"
SFT_FILE = DATA_DIR / "sft_corrections.jsonl"

_lock = Lock()


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def log_preference(
    session_id: str,
    prompt: str,
    chosen: str,
    rejected: str,
    chosen_tool_calls: list[dict[str, Any]] | None = None,
    rejected_tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ensure_data_dir()
    entry = {
        "session_id": session_id,
        "prompt": prompt,
        "chosen": chosen,
        "rejected": rejected,
        "chosen_tool_calls": chosen_tool_calls or [],
        "rejected_tool_calls": rejected_tool_calls or [],
        "timestamp": time.time(),
    }
    with _lock:
        with open(DPO_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


def log_correction(
    session_id: str,
    prompt: str,
    user_message: str,
    original: str,
    corrected: str,
) -> dict[str, Any]:
    ensure_data_dir()
    entry = {
        "session_id": session_id,
        "prompt": prompt,
        "user_message": user_message,
        "original": original,
        "corrected": corrected,
        "timestamp": time.time(),
    }
    with _lock:
        with open(SFT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


def get_stats() -> dict[str, Any]:
    ensure_data_dir()
    dpo_count = 0
    with _lock:
        if DPO_FILE.exists():
            with open(DPO_FILE, "r", encoding="utf-8") as f:
                dpo_count = sum(1 for _ in f)

        sft_count = 0
        if SFT_FILE.exists():
            with open(SFT_FILE, "r", encoding="utf-8") as f:
                sft_count = sum(1 for _ in f)

    return {
        "dpo_count": dpo_count,
        "sft_count": sft_count,
    }


def export_dataset(dataset_type: str) -> list[dict[str, Any]]:
    ensure_data_dir()
    file_path = DPO_FILE if dataset_type == "dpo" else SFT_FILE
    with _lock:
        if not file_path.exists():
            return []
        records = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line.strip()))
    return records
