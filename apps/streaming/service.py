"""Session live streaming orchestration."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.compositor.commands import StartStreamCommand, StopStreamCommand
from apps.compositor.worker_manager import get_session_worker_manager
from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError
from apps.sessions.models import SessionStatus, StudioSession
from apps.sessions.repositories.session_repository import SessionRepository
from apps.streaming.exceptions import (
    IngestManagerNotRunningError,
    InvalidDestinationError,
    StreamAlreadyActiveError,
    StreamNotActiveError,
)
from apps.streaming.models import DestinationType, SessionStream, StreamDestination, StreamStatus
from core import events
from core.webhooks import emit_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamDestinationResult:
    destination_id: uuid.UUID
    url: str
    label: str
    status: str
    started_at: datetime
    stopped_at: datetime | None


@dataclass(frozen=True)
class StreamResult:
    stream_id: uuid.UUID
    session_id: uuid.UUID
    destination_type: str
    destination_url: str
    destination_urls: list[str]
    destinations: list[StreamDestinationResult]
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
        destination_urls: list[str] | None = None,
        destinations: list[dict[str, str]] | None = None,
        tenant_id: str | None = None,
        twitch_chat_enabled: bool = False,
    ) -> StreamResult:
        session = self._get_active_session(session_id)
        self._assert_no_active_stream(session)
        rtmp_entries = self._resolve_rtmp_entries(
            destination_type,
            destination_url=destination_url,
            destination_urls=destination_urls,
            destinations=destinations,
        )
        self._validate_destination(destination_type, rtmp_entries)

        worker_manager = get_session_worker_manager()
        if not worker_manager.is_running(str(session_id)):
            raise IngestManagerNotRunningError(
                'Compositor ingest is not running for this session'
            )

        output_dir = None
        resolved_urls = [url for url, _label in rtmp_entries]
        output_path = ''
        primary_url = ''

        if destination_type == DestinationType.HLS:
            output_dir = self._build_hls_output_dir(session_id)
            output_path = str(output_dir)
            primary_url = str(output_dir / 'playlist.m3u8')
        else:
            primary_url = resolved_urls[0]

        stream = SessionStream.objects.create(
            session=session,
            destination_type=destination_type,
            destination_url=primary_url,
            output_path=output_path,
            status=StreamStatus.LIVE,
            started_at=timezone.now(),
        )

        destination_records: list[StreamDestination] = []
        if destination_type == DestinationType.RTMP:
            for url, label in rtmp_entries:
                destination_records.append(
                    StreamDestination.objects.create(
                        stream=stream,
                        url=url,
                        label=label,
                        status=StreamStatus.LIVE,
                        started_at=timezone.now(),
                    )
                )

        try:
            worker_manager.send_command(
                StartStreamCommand(
                    session_id=str(session_id),
                    destination_type=destination_type,
                    destination_url=primary_url,
                    destination_urls=resolved_urls,
                    output_dir=output_dir,
                )
            )
        except Exception as exc:
            stream.mark_failed()
            stream.save(update_fields=['status', 'stopped_at'])
            for destination in destination_records:
                destination.mark_failed()
                destination.save(update_fields=['status', 'stopped_at'])
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
                'destination_url': primary_url,
                'destination_urls': resolved_urls,
            },
        )
        result = self._to_result(stream)
        if tenant_id and self._should_enable_twitch_chat(
            destination_type=destination_type,
            rtmp_entries=rtmp_entries,
            twitch_chat_enabled=twitch_chat_enabled,
        ):
            self._start_twitch_chat(session_id, tenant_id)
        return result

    def stop_stream(self, session_id: uuid.UUID) -> StreamResult:
        session = self._get_active_session(session_id)
        stream = self._get_active_stream(session)
        self._stop_twitch_chat(session_id)

        worker_manager = get_session_worker_manager()
        if not worker_manager.is_running(str(session_id)):
            stream.mark_failed()
            stream.save(update_fields=['status', 'stopped_at'])
            self._mark_all_destinations(stream, StreamStatus.FAILED)
            raise IngestManagerNotRunningError(
                'Compositor ingest is not running for this session'
            )

        try:
            worker_manager.send_command(
                StopStreamCommand(session_id=str(session_id))
            )
            stream.mark_stopped()
            stream.save(update_fields=['status', 'stopped_at'])
            self._mark_all_destinations(stream, StreamStatus.STOPPED)
        except Exception as exc:
            stream.mark_failed()
            stream.save(update_fields=['status', 'stopped_at'])
            self._mark_all_destinations(stream, StreamStatus.FAILED)
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

        self._stop_twitch_chat(session_id)
        worker_manager = get_session_worker_manager()
        if worker_manager.is_running(str(session_id)) and worker_manager.is_streaming(
            str(session_id)
        ):
            try:
                worker_manager.send_command(
                    StopStreamCommand(session_id=str(session_id))
                )
                stream.mark_stopped()
                self._mark_all_destinations(stream, StreamStatus.STOPPED)
            except Exception:
                stream.mark_failed()
                self._mark_all_destinations(stream, StreamStatus.FAILED)
            stream.save(update_fields=['status', 'stopped_at'])
            return self._to_result(stream)

        stream.mark_failed()
        stream.save(update_fields=['status', 'stopped_at'])
        self._mark_all_destinations(stream, StreamStatus.FAILED)
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

        self._stop_twitch_chat(session_id)
        stream.mark_failed()
        stream.save(update_fields=['status', 'stopped_at'])
        self._mark_all_destinations(stream, StreamStatus.FAILED)
        return self._to_result(stream)

    def mark_stream_destination_failed(
        self,
        session_id: uuid.UUID,
        destination_url: str,
        reason: str,
    ) -> StreamResult | None:
        """Mark one RTMP destination failed; fail the session only when all are down."""
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

        destination = (
            stream.destinations.filter(url=destination_url, status=StreamStatus.LIVE)
            .order_by('-started_at')
            .first()
        )
        if destination is not None:
            destination.mark_failed()
            destination.save(update_fields=['status', 'stopped_at'])

        if stream.destinations.filter(status=StreamStatus.LIVE).exists():
            return self._to_result(stream)

        stream.mark_failed()
        stream.save(update_fields=['status', 'stopped_at'])
        return self._to_result(stream)

    def list_streams(self, session_id: uuid.UUID) -> list[StreamResult]:
        self._get_session(session_id)
        streams = SessionStream.objects.filter(session_id=session_id).prefetch_related(
            'destinations'
        )
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
    def _resolve_rtmp_entries(
        destination_type: str,
        *,
        destination_url: str,
        destination_urls: list[str] | None,
        destinations: list[dict[str, str]] | None,
    ) -> list[tuple[str, str]]:
        if destination_type != DestinationType.RTMP:
            return []

        if destinations:
            entries = [
                (item['url'].strip(), (item.get('label') or '').strip())
                for item in destinations
                if item.get('url', '').strip()
            ]
        elif destination_urls:
            entries = [(url.strip(), '') for url in destination_urls if url.strip()]
        else:
            single = destination_url.strip()
            entries = [(single, '')] if single else []

        if not entries:
            default_url = getattr(settings, 'DEFAULT_RTMP_URL', '')
            if default_url:
                return [(default_url.strip(), '')]
            raise InvalidDestinationError('destination_url is required for RTMP streams')

        max_destinations = getattr(settings, 'STREAMING_MAX_DESTINATIONS', 10)
        if len(entries) > max_destinations:
            raise InvalidDestinationError(
                f'At most {max_destinations} RTMP destinations are allowed'
            )

        deduped: list[tuple[str, str]] = []
        seen: set[str] = set()
        for url, label in entries:
            if url in seen:
                continue
            seen.add(url)
            deduped.append((url, label))
        return deduped

    @staticmethod
    def _validate_destination(
        destination_type: str,
        rtmp_entries: list[tuple[str, str]],
    ) -> None:
        if destination_type not in DestinationType.values:
            raise InvalidDestinationError(f'Unsupported destination type: {destination_type}')

        if destination_type == DestinationType.RTMP:
            for url, _label in rtmp_entries:
                lowered = url.lower()
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
    def _mark_all_destinations(stream: SessionStream, status: str) -> None:
        now = timezone.now()
        for destination in stream.destinations.filter(status=StreamStatus.LIVE):
            destination.status = status
            destination.stopped_at = now
            destination.save(update_fields=['status', 'stopped_at'])

    @staticmethod
    def _to_result(stream: SessionStream) -> StreamResult:
        destination_records = list(stream.destinations.all())
        destination_urls = [item.url for item in destination_records]
        if not destination_urls and stream.destination_url:
            destination_urls = [stream.destination_url]

        return StreamResult(
            stream_id=stream.id,
            session_id=stream.session_id,
            destination_type=stream.destination_type,
            destination_url=stream.destination_url,
            destination_urls=destination_urls,
            destinations=[
                StreamDestinationResult(
                    destination_id=item.id,
                    url=item.url,
                    label=item.label,
                    status=item.status,
                    started_at=item.started_at,
                    stopped_at=item.stopped_at,
                )
                for item in destination_records
            ],
            output_path=stream.output_path,
            status=stream.status,
            started_at=stream.started_at,
            stopped_at=stream.stopped_at,
        )

    @staticmethod
    def _should_enable_twitch_chat(
        *,
        destination_type: str,
        rtmp_entries: list[tuple[str, str]],
        twitch_chat_enabled: bool,
    ) -> bool:
        if not twitch_chat_enabled or destination_type != DestinationType.RTMP:
            return False
        return any('twitch.tv' in url.lower() for url, _label in rtmp_entries)

    @staticmethod
    def _start_twitch_chat(session_id: uuid.UUID, tenant_id: str) -> None:
        try:
            from apps.integrations.twitch_chat.manager import get_twitch_chat_manager

            get_twitch_chat_manager().start(session_id, tenant_id)
        except Exception:
            logger.exception('Failed to start Twitch chat listener for session %s', session_id)

    @staticmethod
    def _stop_twitch_chat(session_id: uuid.UUID) -> None:
        try:
            from apps.integrations.twitch_chat.manager import get_twitch_chat_manager

            get_twitch_chat_manager().stop(session_id)
        except Exception:
            logger.exception('Failed to stop Twitch chat listener for session %s', session_id)
