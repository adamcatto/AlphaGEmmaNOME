from __future__ import annotations

import json
import logging
from pathlib import Path

from smolagents import ToolCallingAgent

from schema import load_settings

from .ollama_model import build_llm
from .sessions import Session
from .streaming import EventBus
from .tools import build_enabled_tools

logger = logging.getLogger(__name__)


def _load_prompts() -> tuple[str, str]:
    prompts_dir: Path = load_settings().paths.prompts_dir
    system = (prompts_dir / "system.md").read_text()
    examples = json.loads((prompts_dir / "few_shot.json").read_text())

    fs_lines = []
    for ex in examples:
        fs_lines.append(f"User: {ex['user']}")
        for step in ex["trace"]:
            fs_lines.append(f"Tool call: {step['tool']}({json.dumps(step['args'])})")
        fs_lines.append(f"Assistant: {ex['final']}")
        fs_lines.append("")
    return system, "\n".join(fs_lines)


def build_agent(session: Session, bus: EventBus) -> ToolCallingAgent:
    s = load_settings()
    model = build_llm()
    tools = build_enabled_tools(session)
    system_prompt, few_shot = _load_prompts()

    def step_callback(step) -> None:
        """Fired by smolagents after each reasoning step.

        smolagents ActionStep exposes `tool_calls` (list), `model_output` (the
        assistant's reasoning text), `observations` (tool-result text), and
        `action_output` (the value returned by final_answer). We surface each
        to the SSE bus so the UI can render progressively.
        """
        try:
            model_output = getattr(step, "model_output", None)
            if isinstance(model_output, str) and model_output.strip():
                bus.emit("thought", {"text": model_output})

            tool_calls = getattr(step, "tool_calls", None) or []
            for tc in tool_calls:
                name = getattr(getattr(tc, "function", None), "name", None) or getattr(tc, "name", None)
                args = getattr(getattr(tc, "function", None), "arguments", None) or getattr(tc, "arguments", None)
                if name:
                    bus.emit("tool_call_start", {"tool": str(name), "arguments": args})

            observation = getattr(step, "observations", None)
            if observation is not None:
                bus.emit("tool_call_result", {"observation": str(observation)[:4000]})

            pending = getattr(session, "pending_viz_spec", None)
            if pending is not None:
                bus.emit("viz_spec", pending)
                session.pending_viz_spec = None

            if getattr(step, "is_final_answer", False):
                action_output = getattr(step, "action_output", None)
                answer = action_output if isinstance(action_output, str) else str(action_output or "")
                if answer:
                    bus.emit("final", {"session_id": session.id, "answer": answer})
        except Exception:
            logger.exception("step_callback error")

    agent = ToolCallingAgent(
        tools=tools,
        model=model,
        max_steps=s.agent.max_steps,
        step_callbacks=[step_callback],
    )

    base = agent.prompt_templates.get("system_prompt", "")
    agent.prompt_templates["system_prompt"] = (
        f"{system_prompt}\n\n---\nExamples:\n{few_shot}\n\n---\n{base}"
    )

    return agent
