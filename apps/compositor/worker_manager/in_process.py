"""In-process session worker runtime (Step 3: GLib main-loop executor)."""

from __future__ import annotations

import logging
from typing import Protocol

from apps.compositor.commands import CommandResult, SessionCommand
from apps.compositor.registry import get as get_ingest_manager
from apps.compositor.registry import register, unregister
from apps.compositor.session_ingest_manager import SessionIngestManager
from apps.compositor.session_producer_poller import stop_session_producer_poller
from apps.compositor.worker_manager.command_executor import get_executor_registry
from apps.compositor.worker_manager.producer_poll import attach_producer_poller
from apps.sessions.models import StudioSession
from integrations.mediasoup.client import MediasoupHttpClient

logger = logging.getLogger(__name__)


class SessionWorkerNotRunningError(LookupError):
    """Raised when no in-process worker exists for a session."""


class InProcessSessionWorkerRuntime:
    """
    Executes session lifecycle and commands against the in-memory registry.

    Step 5: each session owns a producer poller on the worker side.
    """

    def __init__(self) -> None:
        self._executors = get_executor_registry()

    def create_session(
        self,
        session: StudioSession,
        *,
        client: MediasoupHttpClient | None = None,
    ) -> SessionIngestManager:
        def bootstrap() -> SessionIngestManager:
            return SessionIngestManager.create(session, client=client)

        session_id = str(session.id)
        executor = self._executors.attach(session_id, bootstrap)
        ingest_manager = executor.ingest_manager
        assert ingest_manager is not None
        register(ingest_manager)
        attach_producer_poller(
            session_id,
            session_id,
            executor,
            client=client,
        )
        logger.info('Registered in-process session worker for %s', session.id)
        return ingest_manager

    def destroy_session(self, session_id: str) -> None:
        stop_session_producer_poller(session_id)
        executor = self._executors.pop(session_id)
        ingest_manager = unregister(session_id)
        if ingest_manager is None:
            if executor is not None:
                executor.shutdown()
            return

        if executor is not None:
            try:
                executor.submit_callable('stop', ingest_manager.stop)
            except Exception:
                logger.exception(
                    'Failed to stop session worker on executor for %s',
                    session_id,
                )
            executor.shutdown()

        logger.info('Destroyed in-process session worker for %s', session_id)

    def is_running(self, session_id: str) -> bool:
        return get_ingest_manager(session_id) is not None

    def list_running_session_ids(self) -> list[str]:
        return self._executors.session_ids()

    def is_recording(self, session_id: str) -> bool:
        return self._run_query(session_id, lambda manager: manager.is_recording(), False)

    def is_streaming(self, session_id: str) -> bool:
        return self._run_query(session_id, lambda manager: manager.is_streaming(), False)

    def get_rtmp_source_stats(self, session_id: str, source_id: str):
        return self._run_query(
            session_id,
            lambda manager: manager.get_rtmp_source_stats(source_id),
            None,
        )

    def send_command(self, command: SessionCommand) -> CommandResult:
        executor = self._executors.get(command.session_id)
        if executor is None:
            return CommandResult.fail(
                command.command_id,
                f'No session worker running for session {command.session_id}',
            )

        try:
            return executor.submit_command(command)
        except Exception as exc:
            logger.exception(
                'Command %s failed for session %s',
                command.command_type.value,
                command.session_id,
            )
            raise exc

    def _run_query(self, session_id: str, fn, default):
        executor = self._executors.get(session_id)
        ingest_manager = get_ingest_manager(session_id)
        if executor is None or ingest_manager is None:
            return default
        return executor.submit_callable('query', lambda: fn(ingest_manager))


class ISessionWorkerRuntime(Protocol):
    def create_session(
        self,
        session: StudioSession,
        *,
        client: MediasoupHttpClient | None = None,
    ) -> SessionIngestManager | None: ...

    def destroy_session(self, session_id: str) -> None: ...

    def is_running(self, session_id: str) -> bool: ...

    def list_running_session_ids(self) -> list[str]: ...

    def is_recording(self, session_id: str) -> bool: ...

    def is_streaming(self, session_id: str) -> bool: ...

    def get_rtmp_source_stats(self, session_id: str, source_id: str): ...

    def send_command(self, command: SessionCommand) -> CommandResult: ...
