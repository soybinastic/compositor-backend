"""API-side runtime that delegates sessions to Media Supervisor workers via Redis."""

from __future__ import annotations

import logging
import threading

from django.conf import settings

from apps.compositor.commands import (
    CommandResult,
    GetStatusCommand,
    SessionCommand,
)
from apps.compositor.session_ingest_manager import SessionIngestManager
from apps.compositor.worker_manager.command_codec import (
    decode_result,
    decode_session_ingest_status,
    encode_command,
)
from apps.compositor.worker_manager.redis_ipc import (
    RedisClientProtocol,
    clear_session_ready,
    enqueue_session_command,
    get_redis_client,
    publish_supervisor_control,
    wait_for_command_reply,
    wait_for_session_ready,
)
from apps.compositor.worker_manager.resource_allocator import allocate_session_resources
from apps.compositor.worker_manager.session_affinity import (
    clear_session_affinity,
    is_session_expected,
    register_session_affinity,
)
from apps.sessions.models import StudioSession
from integrations.mediasoup.client import MediasoupHttpClient

logger = logging.getLogger(__name__)


class _BufferStats:
    def __init__(self, *, audio_buffers: int, video_buffers: int) -> None:
        self.audio_buffers = audio_buffers
        self.video_buffers = video_buffers


class SupervisorSessionWorkerRuntime:
    """
    Step 4: API sends spawn/destroy to Media Supervisor and commands via Redis.

    GStreamer runs only inside worker subprocesses; the API process never loads
    the compositor pipeline for active sessions.
    """

    def __init__(self, redis_client: RedisClientProtocol | None = None) -> None:
        self._redis = redis_client or get_redis_client()
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self._command_timeout_sec = float(
            getattr(settings, 'SESSION_COMMAND_TIMEOUT_SEC', 30)
        )
        self._spawn_timeout_sec = float(
            getattr(settings, 'SESSION_WORKER_SPAWN_TIMEOUT_SEC', 30)
        )

    def create_session(
        self,
        session: StudioSession,
        *,
        client: MediasoupHttpClient | None = None,
    ) -> SessionIngestManager | None:
        del client  # Worker creates its own mediasoup client.
        session_id = str(session.id)
        affinity = allocate_session_resources(session_id, redis_client=self._redis)
        register_session_affinity(session_id, affinity, redis_client=self._redis)
        publish_supervisor_control(
            self._redis,
            {'action': 'spawn', 'session_id': session_id},
        )
        if not wait_for_session_ready(
            self._redis,
            session_id,
            timeout_sec=self._spawn_timeout_sec,
        ):
            raise RuntimeError(
                f'Session worker for {session_id} did not become ready '
                f'within {self._spawn_timeout_sec}s'
            )

        with self._lock:
            self._running.add(session_id)

        logger.info('Supervisor session worker registered for %s', session_id)
        return None

    def destroy_session(self, session_id: str) -> None:
        with self._lock:
            was_running = session_id in self._running
            self._running.discard(session_id)

        if not was_running:
            clear_session_affinity(session_id, redis_client=self._redis)
            clear_session_ready(self._redis, session_id)
            return

        publish_supervisor_control(
            self._redis,
            {'action': 'destroy', 'session_id': session_id, 'graceful': True},
        )
        clear_session_affinity(session_id, redis_client=self._redis)
        clear_session_ready(self._redis, session_id)
        logger.info('Destroyed supervisor session worker for %s', session_id)

    def is_running(self, session_id: str) -> bool:
        with self._lock:
            tracked = session_id in self._running
        if not tracked:
            return False
        return is_session_expected(session_id, redis_client=self._redis)

    def list_running_session_ids(self) -> list[str]:
        with self._lock:
            return list(self._running)

    def is_recording(self, session_id: str) -> bool:
        status = self._fetch_status(session_id)
        return bool(status and status.recording_active)

    def is_streaming(self, session_id: str) -> bool:
        status = self._fetch_status(session_id)
        return bool(status and status.streaming_active)

    def get_rtmp_source_stats(self, session_id: str, source_id: str):
        status = self._fetch_status(session_id)
        if status is None:
            return None
        for source in status.rtmp_sources:
            if source.source_id == source_id:
                return _BufferStats(
                    audio_buffers=source.audio_buffers,
                    video_buffers=source.video_buffers,
                )
        return None

    def send_command(self, command: SessionCommand) -> CommandResult:
        if not self.is_running(command.session_id):
            return CommandResult.fail(
                command.command_id,
                f'No session worker running for session {command.session_id}',
            )

        enqueue_session_command(
            self._redis,
            command.session_id,
            {
                'action': 'command',
                'command': encode_command(command),
            },
        )
        raw = wait_for_command_reply(
            self._redis,
            command.session_id,
            command.command_id,
            timeout_sec=self._command_timeout_sec,
        )
        if raw is None:
            return CommandResult.fail(
                command.command_id,
                f'Command timed out after {self._command_timeout_sec}s',
            )
        return decode_result(raw)

    def _fetch_status(self, session_id: str):
        if not self.is_running(session_id):
            return None
        result = self.send_command(GetStatusCommand(session_id=session_id))
        if not result.success or result.data is None:
            return None
        if isinstance(result.data, dict):
            return decode_session_ingest_status(result.data)
        return result.data
