from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from smolagents import LiteLLMModel
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
    parse_json_if_needed,
)
from smolagents.utils import parse_json_blob

from schema import load_settings

logger = logging.getLogger(__name__)

_NAME_KEYS = ("name", "function", "tool", "tool_name")
_ARGS_KEYS = ("arguments", "parameters", "args", "input")

# Qwen3 emits <think>...</think> reasoning before every response, and wraps
# tool calls in <tool_call>{...}</tool_call>. These regexes pull each piece out
# so smolagents receives a clean ChatMessage: content = thinking text (so the
# step callback surfaces it as a thought event), tool_calls = parsed.
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL | re.IGNORECASE)


def _extract_name_and_args(d: dict[str, Any]) -> tuple[str, Any]:
    """Pull a (tool_name, tool_arguments) pair out of a dict produced by a small LLM.

    Tolerates the common shapes (name/arguments, function/parameters) plus one
    level of nesting where the real call sits under a wrapper key.
    """
    for nk in _NAME_KEYS:
        v = d.get(nk)
        if isinstance(v, str):
            for ak in _ARGS_KEYS:
                if ak in d:
                    return v, d[ak]
            return v, None
    for v in d.values():
        if isinstance(v, dict):
            try:
                return _extract_name_and_args(v)
            except ValueError:
                continue
    raise ValueError(f"Cannot extract tool name from keys: {list(d.keys())}")


def _parse_tool_calls_from_text(text: str) -> tuple[list[ChatMessageToolCall], str]:
    """Find tool calls in free text.

    Looks for <tool_call>...</tool_call> tags first, then falls back to a bare
    JSON blob. Returns (tool_calls, leftover_text_with_blocks_removed).
    """
    calls: list[ChatMessageToolCall] = []
    remaining = text

    matches = list(_TOOL_CALL_RE.finditer(remaining))
    if matches:
        for m in matches:
            try:
                d = json.loads(m.group(1))
                name, args = _extract_name_and_args(d)
                calls.append(
                    ChatMessageToolCall(
                        id=str(uuid.uuid4()),
                        type="function",
                        function=ChatMessageToolCallFunction(name=name, arguments=args),
                    )
                )
            except Exception as e:
                logger.warning("Failed to parse <tool_call> block: %s", e)
        remaining = _TOOL_CALL_RE.sub("", remaining).strip()
        return calls, remaining

    stripped = remaining.strip()
    if stripped.startswith("{"):
        try:
            d, _ = parse_json_blob(stripped)
            name, args = _extract_name_and_args(d)
            calls.append(
                ChatMessageToolCall(
                    id=str(uuid.uuid4()),
                    type="function",
                    function=ChatMessageToolCallFunction(name=name, arguments=args),
                )
            )
            return calls, ""
        except Exception:
            pass

    return calls, remaining


class TolerantLiteLLMModel(LiteLLMModel):
    """LiteLLM wrapper tuned for small local models.

    Responsibilities:
    - Split Qwen3 <think>...</think> out of content so the thinking text
      becomes `model_output` (→ surfaced as a thought event) while tool calls
      are populated cleanly.
    - Parse <tool_call>{...}</tool_call> tags and raw JSON blobs into
      structured tool_calls, handling Qwen's several JSON shapes and one level
      of nesting.
    """

    def generate(self, *args, **kwargs) -> ChatMessage:
        message = super().generate(*args, **kwargs)
        return self._normalize(message)

    def _normalize(self, message: ChatMessage) -> ChatMessage:
        content = message.content or ""

        think_text = ""
        think_match = _THINK_RE.search(content)
        if think_match:
            think_text = think_match.group(1).strip()
            content = (content[: think_match.start()] + content[think_match.end():]).strip()

        raw = getattr(message, "raw", None)
        if not think_text and raw is not None:
            try:
                reasoning = raw.choices[0].message.reasoning_content
                if isinstance(reasoning, str) and reasoning.strip():
                    think_text = reasoning.strip()
            except Exception:
                pass

        if not message.tool_calls:
            parsed_calls, leftover = _parse_tool_calls_from_text(content)
            if parsed_calls:
                message.tool_calls = parsed_calls
                content = leftover

        if message.tool_calls:
            for tc in message.tool_calls:
                name = tc.function.name
                if isinstance(name, dict):
                    try:
                        inner_name, inner_args = _extract_name_and_args(name)
                    except ValueError:
                        continue
                    tc.function.name = inner_name
                    current = tc.function.arguments
                    if current in (None, "", {}) and inner_args is not None:
                        tc.function.arguments = inner_args
                tc.function.arguments = parse_json_if_needed(tc.function.arguments)

        message.content = think_text or content or ""
        return message

    def parse_tool_calls(self, message: ChatMessage) -> ChatMessage:
        message.role = MessageRole.ASSISTANT
        if not message.tool_calls:
            parsed_calls, _ = _parse_tool_calls_from_text(message.content or "")
            if not parsed_calls:
                raise ValueError(
                    f"No tool call found in model output. Content: {message.content!r}"
                )
            message.tool_calls = parsed_calls
        for tc in message.tool_calls:
            tc.function.arguments = parse_json_if_needed(tc.function.arguments)
        return message


def build_llm() -> LiteLLMModel:
    s = load_settings()
    return TolerantLiteLLMModel(
        model_id=f"ollama_chat/{s.ollama.model}",
        api_base=s.ollama.base_url,
        temperature=s.ollama.temperature,
        max_tokens=s.ollama.max_tokens,
        request_timeout=s.ollama.request_timeout_seconds,
    )
