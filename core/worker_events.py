"""Emit lifecycle events from session workers to the API control plane."""

from __future__ import annotations

import os
from typing import Any

WORKER_PROCESS_ENV = 'COMPOSITOR_WORKER_PROCESS'


def is_worker_process() -> bool:
    return os.environ.get(WORKER_PROCESS_ENV) == '1'


def mark_worker_process() -> None:
    os.environ[WORKER_PROCESS_ENV] = '1'


def emit_worker_event(
    event_type: str,
    session_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """
    Emit a worker-originated event.

    In the API / in-process worker: dispatches immediately on this process.
    In a supervisor worker subprocess: publishes to Redis for the API consumer.
    """
    body = dict(payload or {})
    body.setdefault('session_id', session_id)

    if is_worker_process():
        from apps.compositor.worker_manager.worker_event_ipc import publish_worker_event

        publish_worker_event(event_type, body)
        return

    from core.worker_event_dispatch import dispatch_worker_event

    dispatch_worker_event(event_type, body)
