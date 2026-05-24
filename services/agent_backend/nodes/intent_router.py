from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from schema import load_settings

from ..state import AgentState

logger = logging.getLogger(__name__)

_CLASSIFIER_SYSTEM = """\
You are a routing classifier. Decide whether the user wants a prediction run on a specific genomic region.

Reply with ONLY one word:
- PREDICT — user names a specific gene, locus (chrN:start-end), variant (rsID/HGVS), or upload and wants binding/expression/regulatory/variant-effect analysis. Examples: "where do TFs bind near APOE?", "predict effect of rs429358", "what regulatory elements are in chr7:5000000-5100000?"
- ANSWER — general knowledge, definitions, concepts, small talk. Examples: "what is APOE?", "what is an enhancer?", "hello".

No other words, no punctuation."""


def _build_llm() -> ChatOllama:
    cfg = load_settings().ollama
    return ChatOllama(
        model=cfg.model,
        base_url=cfg.base_url,
        temperature=0.0,
        num_predict=512,
    )


def intent_router_node(state: AgentState) -> dict[str, Any]:
    """Classify the latest user message as 'answer' or 'predict'."""
    user_msg = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        None,
    )
    if user_msg is None:
        return {"intent": "answer"}

    try:
        llm = _build_llm()
        resp = llm.invoke([
            SystemMessage(content=_CLASSIFIER_SYSTEM),
            HumanMessage(content=str(user_msg.content)),
        ])
        text = (resp.content or "").strip().upper()
        tokens = re.findall(r"PREDICT|ANSWER", text)
        intent = "predict" if (tokens and tokens[-1] == "PREDICT") else "answer"
    except Exception:
        logger.exception("intent classifier failed; defaulting to answer")
        intent = "answer"

    logger.info("intent_router: %r → %s", str(user_msg.content)[:80], intent)
    return {"intent": intent}


def route_by_intent(state: AgentState) -> str:
    """Conditional edge: 'answer' or 'predict'."""
    return state.get("intent") or "answer"
