from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langchain_ollama import ChatOllama

from schema import load_settings

from ..state import AgentState

logger = logging.getLogger(__name__)


def build_agent_llm(tools: list[BaseTool]) -> ChatOllama:
    cfg = load_settings().ollama
    llm = ChatOllama(
        model=cfg.model,
        base_url=cfg.base_url,
        temperature=cfg.temperature,
        num_predict=cfg.max_tokens,
    )
    return llm.bind_tools(tools)


def make_agent_node(tools: list[BaseTool], system_prompt: str):
    """Return an agent_node function closed over the LLM + system prompt.

    The LLM is bound once at graph-build time so it isn't reconstructed on
    every graph invocation.
    """
    llm_with_tools = build_agent_llm(tools)

    def agent_node(state: AgentState) -> dict[str, Any]:
        messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
        response = llm_with_tools.invoke(messages)
        return {
            "messages": [response],
            "step_count": state.get("step_count", 0) + 1,
        }

    agent_node.__name__ = "agent_node"
    return agent_node


def should_continue(state: AgentState) -> str:
    """Conditional edge after agent_node: loop to tools or exit to synthesizer."""
    cfg = load_settings().agent
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    if tool_calls and state.get("step_count", 0) < cfg.max_steps:
        return "tools"
    return "synthesizer"
