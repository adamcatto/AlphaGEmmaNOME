from __future__ import annotations

import json
import logging
from typing import Any

from ..state import AgentState

logger = logging.getLogger(__name__)


def synthesizer_node(state: AgentState) -> dict[str, Any]:
    """Collect viz_specs from tool results so they're first-class in state.

    Tool messages carry the raw dict the tool returned as their content.
    We scan for any result that has a 'viz_spec' key and surface them on
    state.viz_specs so the SSE handler can emit them as 'viz_spec' events.
    """
    viz_specs: list[dict[str, Any]] = []
    for msg in state["messages"]:
        if getattr(msg, "type", None) != "tool":
            continue
        content = msg.content
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(content, dict):
            vs = content.get("viz_spec")
            if vs and isinstance(vs, dict):
                viz_specs.append(vs)
    return {"viz_specs": viz_specs}
