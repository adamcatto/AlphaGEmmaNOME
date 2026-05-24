#!/usr/bin/env python
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

# Add services parent to path so we can import feedback_store
sys.path.append(str(Path(__file__).parent.parent))

from services.agent_backend.feedback_store import log_correction, log_preference

# Port of running agent_backend
AGENT_BACKEND_URL = "http://localhost:8003"

CHALLENGES = [
    {
        "locus": "chr17:43125200-43125600",
        "head": "cage",
        "track": 0,
        "mode": "minimize",
        "goal_description": "We want to silence the active promoter by doing a micro-deletion of up to 50bp.",
        "edit_type": "deletion",
    },
    {
        "locus": "chr17:43125200-43125600",
        "head": "atac",
        "track": 2,
        "mode": "maximize",
        "goal_description": "We want to open chromatin accessibility by inserting an activator SP1 motif.",
        "edit_type": "insertion",
        "motif_name": "SP1",
    },
    {
        "locus": "chr17:43125200-43125600",
        "head": "chip_tf",
        "track": 1,
        "mode": "minimize",
        "goal_description": "Ablate or scramble any existing CTCF binding site to disrupt local insulator activity.",
        "edit_type": "motif",
        "motif_name": "CTCF",
    },
    {
        "locus": "chr17:43125200-43125600",
        "head": "rna_seq",
        "track": 0,
        "mode": "maximize",
        "goal_description": "Find synergistic multi-site single nucleotide variants (SNVs, max 2 edits) to increase expression.",
        "edit_type": "snv",
        "max_edits": 2,
    },
]


def run_challenge(challenge: dict) -> None:
    session_id = "auto-rl-generator-session"
    prompt = (
        f"Challenge Locus: {challenge['locus']}\n"
        f"Target Track: {challenge['head']} (idx {challenge['track']})\n"
        f"Goal: {challenge['mode']} the signal.\n"
        f"Edit Type: {challenge['edit_type']}\n"
        f"Description: {challenge['goal_description']}\n\n"
        "Please plan your genomic edits carefully, use the optimize_edits tool, and explain your strategy."
    )

    print(f"\n--- Running Challenge on {challenge['locus']} ({challenge['edit_type']}) ---")
    print(prompt)

    try:
        # 1. Hit the /chat endpoint using SSE stream to capture thoughts and answers
        with httpx.Client(timeout=120.0) as client:
            r = client.post(
                f"{AGENT_BACKEND_URL}/chat",
                json={"message": prompt, "session_id": session_id},
            )
            if r.status_code != 200:
                print(f"Error calling agent backend: {r.status_code} {r.text}")
                return

            # Parse SSE response body
            full_response = ""
            full_thoughts = ""
            tool_calls = []
            
            # Simple line-by-line decoding of Server-Sent Events (SSE)
            lines = r.text.split("\n")
            for line in lines:
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                        event_type = data.get("event")
                        payload = data.get("data")
                        
                        if event_type == "token":
                            full_response += payload.get("text", "")
                        elif event_type == "thought":
                            full_thoughts += payload.get("text", "")
                        elif event_type == "tool_call_start":
                            tool_calls.append(payload)
                        elif event_type == "tool_call_result":
                            if tool_calls:
                                tool_calls[-1]["result"] = payload.get("output")
                    except Exception:
                        pass

            print("\n>> Agent Planning Thoughts:")
            print(full_thoughts or "[No thoughts recorded]")
            print("\n>> Final Answer:")
            print(full_response or "[No response recorded]")

            # 2. Programmatic Oracle evaluation based on AlphaGenome predictions returned by tool
            success = False
            percent_change = 0.0
            
            for tc in tool_calls:
                if tc.get("tool") == "optimize_edits" and "result" in tc:
                    try:
                        res = json.loads(tc["result"])
                        if "candidates" in res and len(res["candidates"]) > 0:
                            best_candidate = res["candidates"][0]
                            percent_change = best_candidate.get("percent_change", 0.0)
                            
                            # Determine success using AlphaGenome simulator predictions as Oracle
                            if challenge["mode"] == "minimize" and percent_change < -10.0:
                                success = True
                            elif challenge["mode"] == "maximize" and percent_change > 10.0:
                                success = True
                            elif challenge["mode"] == "target" and abs(percent_change) < 5.0:
                                success = True
                    except Exception as e:
                        print(f"Error parsing tool output: {e}")

            # 3. Log trajectory to Feedback files based on Oracle evaluation
            if success:
                print(f"\n✅ SUCCESS: Oracle confirmed goal met ({percent_change:+.1f}% change). Logging as SFT Chosen.")
                log_correction(
                    session_id=session_id,
                    prompt=prompt,
                    user_message=prompt,
                    original="Failed or unoptimized plan.",
                    corrected=f"Thoughts: {full_thoughts}\n\nAnswer: {full_response}",
                )
            else:
                print(f"\n❌ REJECTED: Goal not met or insufficient change ({percent_change:+.1f}% change). Logging as DPO preference pair.")
                log_preference(
                    session_id=session_id,
                    prompt=prompt,
                    chosen="[Optimal trajectory with successful edits]",
                    rejected=f"Thoughts: {full_thoughts}\n\nAnswer: {full_response}",
                )

    except Exception as e:
        print(f"Exception during challenge execution: {e}")


def main():
    print("Starting Automated RL Trajectory Generation...")
    print(f"Target Agent Backend URL: {AGENT_BACKEND_URL}")
    for idx, challenge in enumerate(CHALLENGES):
        run_challenge(challenge)


if __name__ == "__main__":
    main()
