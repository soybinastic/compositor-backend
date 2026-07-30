"""API-side facade for compositor session worker lifecycle and commands."""

from __future__ import annotations

import logging

from apps.compositor.commands import CommandResult, GetStatusCommand, SessionCommand
from apps.compositor.session_ingest_manager import SessionIngestManager, SessionIngestStatus
from apps.compositor.worker_manager.in_process import ISessionWorkerRuntime
from apps.compositor.worker_manager.runtime_factory import create_session_worker_runtime
from apps.sessions.models import StudioSession
from integrations.mediasoup.client import MediasoupHttpClient

logger = logging.getLogger(__name__)

_default_manager: SessionWorkerManager | None = None


class SessionWorkerManager:
    """
    Control-plane entry point for session media workers.

    Step 4: selects in-process or supervisor+Redis runtime via settings.
    """

    def __init__(self, runtime: ISessionWorkerRuntime | None = None) -> None:
        self._runtime = runtime or create_session_worker_runtime()

    def create_session(
        self,
        session: StudioSession,
        *,
        client: MediasoupHttpClient | None = None,
    ) -> SessionIngestManager | None:
        return self._runtime.create_session(session, client=client)

    def destroy_session(self, session_id: str) -> None:
        self._runtime.destroy_session(session_id)

    def is_running(self, session_id: str) -> bool:
        return self._runtime.is_running(session_id)

    def list_running_session_ids(self) -> list[str]:
        return self._runtime.list_running_session_ids()

    def is_recording(self, session_id: str) -> bool:
        return self._runtime.is_recording(session_id)

    def is_streaming(self, session_id: str) -> bool:
        return self._runtime.is_streaming(session_id)

    def get_rtmp_source_stats(self, session_id: str, source_id: str):
        return self._runtime.get_rtmp_source_stats(session_id, source_id)

    def send_command(self, command: SessionCommand) -> CommandResult:
        logger.debug(
            'Sending command %s for session %s',
            command.command_type.value,
            command.session_id,
        )
        result = self._runtime.send_command(command)
        if not result.success:
            raise LookupError(result.error or 'Session worker command failed')
        return result

    def get_status(self, session_id: str) -> SessionIngestStatus | None:
        if not self.is_running(session_id):
            return None
        result = self.send_command(GetStatusCommand(session_id=session_id))
        return result.data


def get_session_worker_manager() -> SessionWorkerManager:
    """Return the process-wide SessionWorkerManager singleton."""
    global _default_manager
    if _default_manager is None:
        _default_manager = SessionWorkerManager()
    return _default_manager
