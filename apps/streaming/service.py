"""Session live streaming orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.compositor.registry import get as get_ingest_manager
from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError
from apps.sessions.models import SessionStatus, StudioSession
from apps.sessions.repositories.session_repository import SessionRepository
from apps.streaming.exceptions import (
    IngestManagerNotRunningError,
    InvalidDestinationError,
    StreamAlreadyActiveError,
    StreamNotActiveError,
)
from apps.streaming.models import DestinationType, SessionStream, StreamStatus
from core import events
from core.webhooks import emit_event


@dataclass(frozen=True)
class StreamResult:
    stream_id: uuid.UUID
    session_id: uuid.UUID
    destination_type: str
    destination_url: str
    output_path: str
    status: str
    started_at: datetime
    stopped_at: datetime | None


class StreamingService:
    """Starts and stops compositor live streams to RTMP or HLS destinations."""

    def __init__(self, repository: SessionRepository | None = None) -> None:
        self._repository = repository or SessionRepository()

    def start_stream(
        self,
        session_id: uuid.UUID,
        *,
        destination_type: str,
        destination_url: str = '',
    ) -> StreamResult:
        session = self._get_active_session(session_id)
        self._assert_no_active_stream(session)
        self._validate_destination(destination_type, destination_url)

        ingest_manager = get_ingest_manager(str(session_id))
        if ingest_manager is None:
            raise IngestManagerNotRunningError(
                'Compositor ingest is not running for this session'
            )

        output_dir = None
        resolved_url = destination_url.strip()
        output_path = ''

        if destination_type == DestinationType.HLS:
            output_dir = self._build_hls_output_dir(session_id)
            output_path = str(output_dir)
            resolved_url = str(output_dir / 'playlist.m3u8')
        elif not resolved_url:
            default_url = getattr(settings, 'DEFAULT_RTMP_URL', '')
            if default_url:
                resolved_url = default_url
            else:
                raise InvalidDestinationError('destination_url is required for RTMP streams')

        stream = SessionStream.objects.create(
            session=session,
            destination_type=destination_type,
            destination_url=resolved_url,
            output_path=output_path,
            status=StreamStatus.LIVE,
            started_at=timezone.now(),
        )

        try:
            ingest_manager.start_stream(
                destination_type=destination_type,
                destination_url=resolved_url,
                output_dir=output_dir,
            )
        except Exception as exc:
            stream.mark_failed()
            stream.save(update_fields=['status', 'stopped_at'])
            emit_event(
                events.STREAM_FAILED,
                {
                    'session_id': str(session_id),
                    'stream_id': str(stream.id),
                    'destination_type': destination_type,
                    'error': str(exc),
                },
            )
            raise

        emit_event(
            events.STREAM_STARTED,
            {
                'session_id': str(session_id),
                'stream_id': str(stream.id),
                'destination_type': destination_type,
                'destination_url': resolved_url,
            },
        )
        return self._to_result(stream)

    def stop_stream(self, session_id: uuid.UUID) -> StreamResult:
        session = self._get_active_session(session_id)
        stream = self._get_active_stream(session)

        ingest_manager = get_ingest_manager(str(session_id))
        if ingest_manager is None:
            stream.mark_failed()
            stream.save(update_fields=['status', 'stopped_at'])
            raise IngestManagerNotRunningError(
                'Compositor ingest is not running for this session'
            )

        try:
            ingest_manager.stop_stream()
            stream.mark_stopped()
            stream.save(update_fields=['status', 'stopped_at'])
        except Exception as exc:
            stream.mark_failed()
            stream.save(update_fields=['status', 'stopped_at'])
            emit_event(
                events.STREAM_FAILED,
                {
                    'session_id': str(session_id),
                    'stream_id': str(stream.id),
                    'error': str(exc),
                },
            )
            raise

        emit_event(
            events.STREAM_STOPPED,
            {
                'session_id': str(session_id),
                'stream_id': str(stream.id),
                'destination_url': stream.destination_url,
            },
        )
        return self._to_result(stream)

    def stop_active_stream_if_any(self, session_id: uuid.UUID) -> StreamResult | None:
        """Finalize a live stream during session teardown."""
        stream = (
            SessionStream.objects.filter(
                session_id=session_id,
                status=StreamStatus.LIVE,
            )
            .order_by('-started_at')
            .first()
        )
        if stream is None:
            return None

        ingest_manager = get_ingest_manager(str(session_id))
        if ingest_manager is not None and ingest_manager.is_streaming():
            try:
                ingest_manager.stop_stream()
                stream.mark_stopped()
            except Exception:
                stream.mark_failed()
            stream.save(update_fields=['status', 'stopped_at'])
            return self._to_result(stream)

        stream.mark_failed()
        stream.save(update_fields=['status', 'stopped_at'])
        return self._to_result(stream)

    def mark_active_stream_failed(self, session_id: uuid.UUID, reason: str) -> StreamResult | None:
        """Mark the live stream failed after unrecoverable RTMP errors."""
        stream = (
            SessionStream.objects.filter(
                session_id=session_id,
                status=StreamStatus.LIVE,
            )
            .order_by('-started_at')
            .first()
        )
        if stream is None:
            return None

        stream.mark_failed()
        stream.save(update_fields=['status', 'stopped_at'])
        return self._to_result(stream)

    def list_streams(self, session_id: uuid.UUID) -> list[StreamResult]:
        self._get_session(session_id)
        streams = SessionStream.objects.filter(session_id=session_id)
        return [self._to_result(stream) for stream in streams]

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
    def _validate_destination(destination_type: str, destination_url: str) -> None:
        if destination_type not in DestinationType.values:
            raise InvalidDestinationError(f'Unsupported destination type: {destination_type}')

        if destination_type == DestinationType.RTMP and destination_url:
            lowered = destination_url.lower()
            if not (lowered.startswith('rtmp://') or lowered.startswith('rtmps://')):
                raise InvalidDestinationError(
                    'RTMP destination_url must start with rtmp:// or rtmps://'
                )

    @staticmethod
    def _assert_no_active_stream(session: StudioSession) -> None:
        if SessionStream.objects.filter(
            session=session,
            status=StreamStatus.LIVE,
        ).exists():
            raise StreamAlreadyActiveError('A stream is already live for this session')

    @staticmethod
    def _get_active_stream(session: StudioSession) -> SessionStream:
        stream = (
            SessionStream.objects.filter(
                session=session,
                status=StreamStatus.LIVE,
            )
            .order_by('-started_at')
            .first()
        )
        if stream is None:
            raise StreamNotActiveError('No active stream for this session')
        return stream

    @staticmethod
    def _build_hls_output_dir(session_id: uuid.UUID) -> Path:
        timestamp = timezone.now().strftime('%Y%m%dT%H%M%SZ')
        return Path(settings.STREAMING_HLS_DIR) / str(session_id) / timestamp

    @staticmethod
    def _to_result(stream: SessionStream) -> StreamResult:
        return StreamResult(
            stream_id=stream.id,
            session_id=stream.session_id,
            destination_type=stream.destination_type,
            destination_url=stream.destination_url,
            output_path=stream.output_path,
            status=stream.status,
            started_at=stream.started_at,
            stopped_at=stream.stopped_at,
        )
