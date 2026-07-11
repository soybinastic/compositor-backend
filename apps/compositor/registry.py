"""Registry of active session ingest managers."""

from __future__ import annotations

import threading

from apps.compositor.session_ingest_manager import SessionIngestManager

_lock = threading.Lock()
_managers: dict[str, SessionIngestManager] = {}


def register(manager: SessionIngestManager) -> None:
    with _lock:
        _managers[manager.session_id] = manager


def get(session_id: str) -> SessionIngestManager | None:
    with _lock:
        return _managers.get(session_id)


def unregister(session_id: str) -> SessionIngestManager | None:
    with _lock:
        return _managers.pop(session_id, None)


def all_managers() -> list[SessionIngestManager]:
    with _lock:
        return list(_managers.values())


def clear_all() -> None:
    with _lock:
        _managers.clear()
