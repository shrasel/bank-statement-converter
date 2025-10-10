from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import Request, Response

from app.core.config import get_settings


@dataclass
class SessionEntry:
    data: Dict[str, object]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


class SessionStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: Dict[str, SessionEntry] = {}

    def set(self, session_id: str, data: Dict[str, object]) -> None:
        with self._lock:
            entry = SessionEntry(data=data)
            self._store[session_id] = entry

    def update(self, session_id: str, data: Dict[str, object]) -> None:
        with self._lock:
            entry = self._store.get(session_id)
            if entry:
                entry.data.update(data)
                entry.touch()
            else:
                self._store[session_id] = SessionEntry(data=data)

    def get(self, session_id: str) -> Optional[Dict[str, object]]:
        with self._lock:
            entry = self._store.get(session_id)
            if not entry:
                return None
            if self._is_expired(entry):
                del self._store[session_id]
                return None
            entry.touch()
            return dict(entry.data)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)

    def exists(self, session_id: str) -> bool:
        with self._lock:
            entry = self._store.get(session_id)
            if not entry:
                return False
            if self._is_expired(entry):
                del self._store[session_id]
                return False
            return True

    def cleanup(self) -> int:
        removed = 0
        with self._lock:
            for key in list(self._store.keys()):
                if self._is_expired(self._store[key]):
                    del self._store[key]
                    removed += 1
        return removed

    def _is_expired(self, entry: SessionEntry) -> bool:
        if self._ttl <= 0:
            return False
        now = datetime.now(timezone.utc)
        return (now - entry.updated_at).total_seconds() > self._ttl


class SessionManager:
    def __init__(self, store: SessionStore, cookie_name: str, cookie_max_age: int) -> None:
        self.store = store
        self.cookie_name = cookie_name
        self.cookie_max_age = cookie_max_age

    def get_or_create_session_id(self, request: Request, response: Response) -> str:
        session_id = request.cookies.get(self.cookie_name)
        if session_id and self.store.exists(session_id):
            return session_id
        session_id = uuid.uuid4().hex
        self.store.set(session_id, {})
        self._set_cookie(response, session_id)
        return session_id

    def ensure_session(self, request: Request, response: Response) -> str:
        session_id = request.cookies.get(self.cookie_name)
        if session_id and self.store.exists(session_id):
            self._set_cookie(response, session_id)
            return session_id
        return self.get_or_create_session_id(request, response)

    def clear_session(self, response: Response, session_id: Optional[str]) -> None:
        if session_id:
            self.store.clear(session_id)
        response.delete_cookie(self.cookie_name)

    def _set_cookie(self, response: Response, session_id: str) -> None:
        response.set_cookie(
            key=self.cookie_name,
            value=session_id,
            httponly=True,
            samesite="lax",
            max_age=self.cookie_max_age,
            secure=False,
        )


settings = get_settings()
session_store = SessionStore(ttl_seconds=settings.session_ttl_seconds)
session_manager = SessionManager(
    store=session_store,
    cookie_name=settings.session_cookie_name,
    cookie_max_age=settings.session_cookie_max_age,
)


def schedule_cleanup() -> int:
    """Purge expired sessions and return count removed."""
    return session_store.cleanup()
