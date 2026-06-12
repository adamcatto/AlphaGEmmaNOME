"""LangGraph StateGraph for AlphaGEmmaNOME.

Topology:
    START → intent_router
              ├─ "answer"  → conversational → END
              └─ "predict" → agent ⇄ tools → synthesizer → END

agent and tools form a ReAct loop. agent_node calls the LLM; if it emits
tool_calls the ToolNode executes them and appends ToolMessages; the loop
continues until no tool calls or max_steps is reached, then synthesizer runs.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from schema import load_settings

from .nodes.agent_node import make_agent_node, should_continue
from .nodes.conversational import conversational_node
from .nodes.intent_router import intent_router_node, route_by_intent
from .nodes.synthesizer import synthesizer_node
from .state import AgentState

logger = logging.getLogger(__name__)


def _load_system_prompt() -> str:
    prompts_dir: Path = load_settings().paths.prompts_dir
    system = (prompts_dir / "system.md").read_text()

    # Append few-shot examples if they exist.
    few_shot_path = prompts_dir / "few_shot.json"
    if few_shot_path.exists():
        examples = json.loads(few_shot_path.read_text())
        lines: list[str] = ["\n\n---\nExamples:"]
        for ex in examples:
            lines.append(f"User: {ex['user']}")
            for step in ex.get("trace", []):
                lines.append(f"Tool call: {step['tool']}({json.dumps(step['args'])})")
            lines.append(f"Assistant: {ex['final']}")
            lines.append("")
        system += "\n".join(lines)

    return system


def build_graph(tools: list[StructuredTool] | None = None):
    """Build and compile the LangGraph StateGraph.

    `tools` can be pre-built (for testing); if None, build_tool_list() is
    called to respect config/tools.yaml toggles.
    """
    if tools is None:
        from .tools.langchain_adapter import build_tool_list
        tools = build_tool_list()

    system_prompt = _load_system_prompt()
    agent_node = make_agent_node(tools, system_prompt)
    tool_node = ToolNode(tools)

    graph = StateGraph(AgentState)

    graph.add_node("intent_router", intent_router_node)
    graph.add_node("conversational", conversational_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_node("synthesizer", synthesizer_node)

    graph.add_edge(START, "intent_router")
    graph.add_conditional_edges(
        "intent_router",
        route_by_intent,
        {"answer": "conversational", "predict": "agent"},
    )
    graph.add_edge("conversational", END)
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "synthesizer": "synthesizer"},
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("synthesizer", END)

    return graph.compile()


@lru_cache(maxsize=1)
def get_graph():
    """Return the singleton compiled graph (built once, cached for the process)."""
    return build_graph()
