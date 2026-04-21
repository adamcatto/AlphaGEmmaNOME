from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import SystemMessage
from langchain_ollama import ChatOllama

from schema import load_settings

from ..state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are a helpful functional-genomics research assistant.

Answer the user's question directly and concisely using your own knowledge.
If they ask about a gene, variant, locus, or uploaded sequence and want
model-driven predictions (binding, expression, regulatory state, variant
effect), tell them you can run AlphaGenome on it — but do not invent
prediction numbers.

Be conversational and to the point. Prefer 2–4 short paragraphs over bullet
lists for simple questions."""


def _build_llm() -> ChatOllama:
    cfg = load_settings().ollama
    return ChatOllama(
        model=cfg.model,
        base_url=cfg.base_url,
        temperature=cfg.temperature,
        num_predict=cfg.max_tokens,
    )


def conversational_node(state: AgentState) -> dict[str, Any]:
    """Stream a direct LLM answer for non-predict queries.

    The LLM call streams via astream_events at the graph level; this node
    just invokes synchronously and returns the AIMessage so the final answer
    lands in messages.
    """
    llm = _build_llm()
    messages = [SystemMessage(content=_SYSTEM)] + list(state["messages"])
    response = llm.invoke(messages)
    return {"messages": [response]}
