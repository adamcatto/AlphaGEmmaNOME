from __future__ import annotations

from typing import Any

import httpx
from smolagents import Tool

from schema import load_settings


class SessionAwareTool(Tool):
    def __init__(self, session_context: Any = None):
        super().__init__()
        self.session_context = session_context


_http: httpx.Client | None = None


def http_client() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(timeout=load_settings().ollama.request_timeout_seconds)
    return _http
