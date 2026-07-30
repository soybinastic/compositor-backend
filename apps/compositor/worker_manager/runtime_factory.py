"""Construct the configured session worker runtime."""

from __future__ import annotations

from django.conf import settings

from apps.compositor.worker_manager.in_process import (
    ISessionWorkerRuntime,
    InProcessSessionWorkerRuntime,
)
from apps.compositor.worker_manager.supervisor_runtime import (
    SupervisorSessionWorkerRuntime,
)


def create_session_worker_runtime() -> ISessionWorkerRuntime:
    mode = getattr(settings, 'COMPOSITOR_WORKER_MODE', 'inprocess')
    if mode == 'supervisor':
        return SupervisorSessionWorkerRuntime()
    if mode != 'inprocess':
        raise ValueError(
            f'Unsupported COMPOSITOR_WORKER_MODE={mode!r}; '
            "expected 'inprocess' or 'supervisor'"
        )
    return InProcessSessionWorkerRuntime()
