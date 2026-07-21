"""RTMP pull source orchestration for compositor ingest."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from apps.compositor.registry import get as get_ingest_manager
from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError
from apps.sessions.models import SessionStatus, StudioSession
from apps.sessions.repositories.session_repository import SessionRepository
from apps.sources.exceptions import (
    IngestManagerNotRunningError,
    InvalidRtmpUrlError,
    RtmpSourceNotFoundError,
)
from apps.sources.models import RtmpSourceStatus, SessionRtmpSource
from core import events
from core.webhooks import emit_event


@dataclass(frozen=True)
class RtmpSourceResult:
    source_id: str
    session_id: uuid.UUID
    url: str
    display_name: str
    status: str
    started_at: datetime
    stopped_at: datetime | None
    video_buffers: int = 0
    audio_buffers: int = 0


class RtmpSourceService:
    """Adds and removes RTMP pull sources on the compositor pipeline."""

    def __init__(self, repository: SessionRepository | None = None) -> None:
        self._repository = repository or SessionRepository()

    def add_source(
        self,
        session_id: uuid.UUID,
        *,
        url: str,
        display_name: str = '',
    ) -> RtmpSourceResult:
        session = self._get_active_session(session_id)
        normalized_url = self._validate_url(url)

        ingest_manager = get_ingest_manager(str(session_id))
        if ingest_manager is None:
            raise IngestManagerNotRunningError(
                'Compositor ingest is not running for this session'
            )

        source_id = f'rtmp-{uuid.uuid4().hex[:12]}'
        record = SessionRtmpSource.objects.create(
            session=session,
            source_id=source_id,
            url=normalized_url,
            display_name=display_name.strip(),
            status=RtmpSourceStatus.ACTIVE,
            started_at=timezone.now(),
        )

        try:
            ingest_manager.add_rtmp_source(
                source_id=source_id,
                url=normalized_url,
                display_name=record.display_name,
            )
        except Exception as exc:
            record.mark_failed()
            record.save(update_fields=['status', 'stopped_at'])
            emit_event(
                events.RTMP_SOURCE_FAILED,
                {
                    'session_id': str(session_id),
                    'source_id': source_id,
                    'url': normalized_url,
                    'error': str(exc),
                },
            )
            raise

        emit_event(
            events.RTMP_SOURCE_STARTED,
            {
                'session_id': str(session_id),
                'source_id': source_id,
                'url': normalized_url,
            },
        )
        return self._to_result(record, ingest_manager)

    def remove_source(self, session_id: uuid.UUID, source_id: str) -> RtmpSourceResult:
        session = self._get_active_session(session_id)
        record = self._get_active_record(session, source_id)

        ingest_manager = get_ingest_manager(str(session_id))
        if ingest_manager is not None:
            ingest_manager.remove_rtmp_source(source_id)

        record.mark_stopped()
        record.save(update_fields=['status', 'stopped_at'])

        emit_event(
            events.RTMP_SOURCE_STOPPED,
            {
                'session_id': str(session_id),
                'source_id': source_id,
                'url': record.url,
            },
        )
        return self._to_result(record, ingest_manager)

    def list_sources(self, session_id: uuid.UUID) -> list[RtmpSourceResult]:
        session = self._get_session(session_id)
        ingest_manager = get_ingest_manager(str(session_id))
        records = SessionRtmpSource.objects.filter(session=session)
        return [self._to_result(record, ingest_manager) for record in records]

    def stop_active_sources_if_any(self, session_id: uuid.UUID) -> None:
        ingest_manager = get_ingest_manager(str(session_id))
        active_records = SessionRtmpSource.objects.filter(
            session_id=session_id,
            status=RtmpSourceStatus.ACTIVE,
        )
        for record in active_records:
            if ingest_manager is not None:
                try:
                    ingest_manager.remove_rtmp_source(record.source_id)
                except Exception:
                    record.mark_failed()
                    record.save(update_fields=['status', 'stopped_at'])
                    continue
            record.mark_stopped()
            record.save(update_fields=['status', 'stopped_at'])

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
    def _validate_url(url: str) -> str:
        normalized = url.strip()
        lowered = normalized.lower()
        if not (lowered.startswith('rtmp://') or lowered.startswith('rtmps://')):
            raise InvalidRtmpUrlError('url must start with rtmp:// or rtmps://')
        return normalized

    @staticmethod
    def _get_active_record(session: StudioSession, source_id: str) -> SessionRtmpSource:
        record = SessionRtmpSource.objects.filter(
            session=session,
            source_id=source_id,
            status=RtmpSourceStatus.ACTIVE,
        ).first()
        if record is None:
            raise RtmpSourceNotFoundError(f'RTMP source {source_id} not found')
        return record

    @staticmethod
    def _to_result(
        record: SessionRtmpSource,
        ingest_manager,
    ) -> RtmpSourceResult:
        video_buffers = 0
        audio_buffers = 0
        if ingest_manager is not None:
            stats = ingest_manager.get_rtmp_source_stats(record.source_id)
            if stats is not None:
                video_buffers = stats.video_buffers
                audio_buffers = stats.audio_buffers

        return RtmpSourceResult(
            source_id=record.source_id,
            session_id=record.session_id,
            url=record.url,
            display_name=record.display_name,
            status=record.status,
            started_at=record.started_at,
            stopped_at=record.stopped_at,
            video_buffers=video_buffers,
            audio_buffers=audio_buffers,
        )
