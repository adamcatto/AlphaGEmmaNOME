from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from schema import load_settings


@dataclass
class Session:
    id: str
    created_at: float
    last_used_at: float
    history: list[dict[str, Any]] = field(default_factory=list)
    uploads: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_prediction: dict[str, Any] | None = None
    pending_viz_spec: dict[str, Any] | None = None
    bus: Any = None  # EventBus for the active chat; set in /chat, cleared on exit.


class SessionStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        s = Session(id=str(uuid.uuid4()), created_at=time.time(), last_used_at=time.time())
        with self._lock:
            self._sessions[s.id] = s
        return s

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is not None:
                s.last_used_at = time.time()
            return s

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id:
            s = self.get(session_id)
            if s is not None:
                return s
        return self.create()

    def gc(self) -> int:
        ttl = load_settings().limits.session_ttl_seconds
        now = time.time()
        removed = 0
        with self._lock:
            for sid in list(self._sessions):
                if now - self._sessions[sid].last_used_at > ttl:
                    del self._sessions[sid]
                    removed += 1
        return removed


STORE = SessionStore()
