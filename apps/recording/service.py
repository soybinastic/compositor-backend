"""Session recording orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.compositor.registry import get as get_ingest_manager
from apps.recording.exceptions import (
    IngestManagerNotRunningError,
    RecordingAlreadyActiveError,
    RecordingNotActiveError,
)
from apps.recording.models import RecordingStatus, SessionRecording
from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError
from apps.sessions.models import SessionStatus, StudioSession
from apps.sessions.repositories.session_repository import SessionRepository
from core import events
from core.webhooks import emit_event


@dataclass(frozen=True)
class RecordingResult:
    recording_id: uuid.UUID
    session_id: uuid.UUID
    status: str
    file_path: str
    started_at: datetime
    stopped_at: datetime | None


class RecordingService:
    """Starts and stops composited MP4 recordings for active sessions."""

    def __init__(self, repository: SessionRepository | None = None) -> None:
        self._repository = repository or SessionRepository()

    def start_recording(self, session_id: uuid.UUID) -> RecordingResult:
        session = self._get_active_session(session_id)
        self._assert_no_active_recording(session)

        ingest_manager = get_ingest_manager(str(session_id))
        if ingest_manager is None:
            raise IngestManagerNotRunningError(
                'Compositor ingest is not running for this session'
            )

        file_path = self._build_output_path(session_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        recording = SessionRecording.objects.create(
            session=session,
            status=RecordingStatus.RECORDING,
            file_path=str(file_path),
            started_at=timezone.now(),
        )

        try:
            ingest_manager.start_recording(file_path)
        except Exception as exc:
            recording.mark_failed()
            recording.save(update_fields=['status', 'stopped_at'])
            if file_path.exists():
                file_path.unlink(missing_ok=True)
            emit_event(
                events.RECORDING_FAILED,
                {
                    'session_id': str(session_id),
                    'recording_id': str(recording.id),
                    'error': str(exc),
                },
            )
            raise

        emit_event(
            events.RECORDING_STARTED,
            {
                'session_id': str(session_id),
                'recording_id': str(recording.id),
                'file_path': str(file_path),
            },
        )
        return self._to_result(recording)

    def stop_recording(self, session_id: uuid.UUID) -> RecordingResult:
        session = self._get_active_session(session_id)
        recording = self._get_active_recording(session)

        ingest_manager = get_ingest_manager(str(session_id))
        if ingest_manager is None:
            recording.mark_failed()
            recording.save(update_fields=['status', 'stopped_at'])
            raise IngestManagerNotRunningError(
                'Compositor ingest is not running for this session'
            )

        try:
            ingest_manager.stop_recording()
            recording.mark_stopped()
            recording.save(update_fields=['status', 'stopped_at'])
        except Exception as exc:
            recording.mark_failed()
            recording.save(update_fields=['status', 'stopped_at'])
            emit_event(
                events.RECORDING_FAILED,
                {
                    'session_id': str(session_id),
                    'recording_id': str(recording.id),
                    'error': str(exc),
                },
            )
            raise

        emit_event(
            events.RECORDING_STOPPED,
            {
                'session_id': str(session_id),
                'recording_id': str(recording.id),
                'file_path': recording.file_path,
            },
        )
        return self._to_result(recording)

    def stop_active_recording_if_any(self, session_id: uuid.UUID) -> RecordingResult | None:
        """Finalize an in-progress recording during session teardown."""
        recording = (
            SessionRecording.objects.filter(
                session_id=session_id,
                status=RecordingStatus.RECORDING,
            )
            .order_by('-started_at')
            .first()
        )
        if recording is None:
            return None

        ingest_manager = get_ingest_manager(str(session_id))
        if ingest_manager is not None and ingest_manager.is_recording():
            try:
                ingest_manager.stop_recording()
                recording.mark_stopped()
            except Exception:
                recording.mark_failed()
            recording.save(update_fields=['status', 'stopped_at'])
            return self._to_result(recording)

        recording.mark_failed()
        recording.save(update_fields=['status', 'stopped_at'])
        return self._to_result(recording)

    def list_recordings(self, session_id: uuid.UUID) -> list[RecordingResult]:
        self._get_session(session_id)
        recordings = SessionRecording.objects.filter(session_id=session_id)
        return [self._to_result(recording) for recording in recordings]

    def _get_session(self, session_id: uuid.UUID) -> StudioSession:
        session = self._repository.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(f'Session {session_id} not found')
        return session

    def _get_active_session(self, session_id: uuid.UUID) -> StudioSession:
        session = self._get_session(session_id)
        if session.status == SessionStatus.ENDED:
            raise SessionEndedError('Session has ended')
        return session

    @staticmethod
    def _assert_no_active_recording(session: StudioSession) -> None:
        if SessionRecording.objects.filter(
            session=session,
            status=RecordingStatus.RECORDING,
        ).exists():
            raise RecordingAlreadyActiveError('A recording is already in progress')

    @staticmethod
    def _get_active_recording(session: StudioSession) -> SessionRecording:
        recording = (
            SessionRecording.objects.filter(
                session=session,
                status=RecordingStatus.RECORDING,
            )
            .order_by('-started_at')
            .first()
        )
        if recording is None:
            raise RecordingNotActiveError('No active recording for this session')
        return recording

    @staticmethod
    def _build_output_path(session_id: uuid.UUID) -> Path:
        timestamp = timezone.now().strftime('%Y%m%dT%H%M%SZ')
        filename = f'{session_id}_{timestamp}.mp4'
        return Path(settings.RECORDINGS_DIR) / filename

    @staticmethod
    def _to_result(recording: SessionRecording) -> RecordingResult:
        return RecordingResult(
            recording_id=recording.id,
            session_id=recording.session_id,
            status=recording.status,
            file_path=recording.file_path,
            started_at=recording.started_at,
            stopped_at=recording.stopped_at,
        )
