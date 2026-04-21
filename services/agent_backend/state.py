from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """Typed state threaded through the LangGraph execution.

    messages:       Full conversation including tool calls and results.
    session_id:     Stable identifier for the Session in STORE; tools resolve
                    session context from this via RunnableConfig.
    intent:         Set by intent_router_node; controls the conditional branch.
    viz_specs:      Accumulated viz_spec dicts from tool outputs; reducer appends
                    so parallel tool calls are merged without overwriting.
    step_count:     Incremented by agent_node on each LLM step; used as a
                    belt-and-suspenders guard against infinite loops.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    session_id: str
    intent: Literal["answer", "predict"] | None
    viz_specs: Annotated[list[dict[str, Any]], operator.add]
    step_count: int
