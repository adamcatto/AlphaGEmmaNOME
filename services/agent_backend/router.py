from __future__ import annotations

import logging
import re

import litellm

from schema import load_settings

from .streaming import EventBus

logger = logging.getLogger(__name__)


# Qwen3:4b ignores /no_think and always produces a <think> block. Rather than
# fight it, we use the ollama_chat/ prefix (so LiteLLM routes via Ollama's
# /api/chat, which splits thinking into a separate field) and size max_tokens
# large enough to fit the thinking plus the final one-word answer.
_CLASSIFIER_SYSTEM = """You are a routing classifier. Decide whether the user wants a prediction run on a specific genomic region.

Reply with ONLY one word:
- PREDICT — user names a specific gene, locus (chrN:start-end), variant (rsID/HGVS), or upload and wants binding/expression/regulatory/variant-effect analysis. Examples: "where do TFs bind near APOE?", "predict effect of rs429358", "what regulatory elements are in chr7:5000000-5100000?"
- ANSWER — general knowledge, definitions, concepts, small talk. Examples: "what is APOE?", "what is an enhancer?", "hello".

No other words, no punctuation."""


_CONVERSATIONAL_SYSTEM = """You are a helpful functional-genomics research assistant.

Answer the user's question directly and concisely using your own knowledge.
If they ask about a gene, variant, locus, or uploaded sequence and want
model-driven predictions (binding, expression, regulatory state, variant
effect), tell them you can run AlphaGenome on it — but do not invent
prediction numbers.

Be conversational and to the point. Prefer 2–4 short paragraphs over bullet
lists for simple questions."""


def _litellm_kwargs() -> dict:
    s = load_settings().ollama
    return {
        "model": f"ollama_chat/{s.model}",
        "api_base": s.base_url,
        "temperature": s.temperature,
        "timeout": s.request_timeout_seconds,
    }


def classify(message: str) -> str:
    """Return 'PREDICT' or 'ANSWER'. Falls back to 'ANSWER' on error —
    better to answer conversationally than to loop on tool calls."""
    try:
        resp = litellm.completion(
            messages=[
                {"role": "system", "content": _CLASSIFIER_SYSTEM},
                {"role": "user", "content": message},
            ],
            max_tokens=1024,
            **_litellm_kwargs(),
        )
        text = (resp.choices[0].message.content or "").strip().upper()
        tokens = re.findall(r"PREDICT|ANSWER", text)
        if tokens and tokens[-1] == "PREDICT":
            return "PREDICT"
        return "ANSWER"
    except Exception:
        logger.exception("classifier failed; defaulting to ANSWER")
        return "ANSWER"


def stream_conversational(message: str, history: list[dict], bus: EventBus, session_id: str) -> str:
    """Stream a direct LLM answer to the bus as `token` events. Returns the
    full answer once complete."""
    messages = [{"role": "system", "content": _CONVERSATIONAL_SYSTEM}]
    for turn in history[-10:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": message})

    chunks: list[str] = []
    # With ollama_chat/, Qwen3 thinking arrives on deltas as reasoning_content,
    # separate from the final-answer content. We forward reasoning as `thought`
    # events so the UI's collapsible reasoning block fills in live. The inline
    # <think>...</think> state machine stays as a safety net in case a provider
    # flips back to embedding reasoning in content.
    in_think = False
    pending = ""
    try:
        stream = litellm.completion(
            messages=messages,
            max_tokens=load_settings().ollama.max_tokens,
            stream=True,
            **_litellm_kwargs(),
        )
        for chunk in stream:
            delta_obj = chunk.choices[0].delta
            reasoning = getattr(delta_obj, "reasoning_content", None) or ""
            if reasoning:
                bus.emit("thought", {"text": reasoning})
            delta = delta_obj.content or ""
            if not delta:
                continue
            pending += delta
            while pending:
                if in_think:
                    end = pending.find("</think>")
                    if end < 0:
                        pending = ""
                        break
                    pending = pending[end + len("</think>"):]
                    in_think = False
                else:
                    start = pending.find("<think>")
                    if start < 0:
                        chunks.append(pending)
                        bus.emit("token", {"text": pending})
                        pending = ""
                        break
                    if start > 0:
                        head = pending[:start]
                        chunks.append(head)
                        bus.emit("token", {"text": head})
                    pending = pending[start + len("<think>"):]
                    in_think = True
    except Exception as e:
        logger.exception("streaming failed")
        bus.emit("error", {"session_id": session_id, "error": str(e)})
        return ""

    answer = "".join(chunks)
    bus.emit("final", {"session_id": session_id, "answer": answer})
    return answer
