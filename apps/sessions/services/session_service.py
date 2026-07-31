from __future__ import annotations

import uuid
from dataclasses import dataclass

from apps.sessions.exceptions import (
    InvalidInviteTokenError,
    SessionEndedError,
    SessionNotFoundError,
)
from apps.sessions.models import LayoutType, SessionStatus, StudioSession
from apps.compositor.tile_order import (
    merge_tile_order_config,
    sanitize_hidden_source_ids,
)
from apps.sessions.repositories.session_repository import SessionRepository
from apps.sessions.services.invite_service import InviteService
from apps.sessions.services.mediasoup_bootstrap import MediasoupMediaPlaneBootstrap
from core.exceptions import MediasoupConnectionError
from core import events
from core.interfaces import IMediaPlaneBootstrap
from core.webhooks import emit_event
from integrations.mediasoup.exceptions import MediasoupApiError


@dataclass(frozen=True)
class SessionCreateResult:
    session: StudioSession
    invite_url: str
    mediasoup_ws_url: str


@dataclass(frozen=True)
class InviteValidationResult:
    session_id: str
    room_id: str
    mediasoup_ws_url: str
    layout: str
    host_display_name: str


class SessionService:
    """Orchestrates studio session lifecycle."""

    def __init__(
        self,
        repository: SessionRepository | None = None,
        invite_service: InviteService | None = None,
        media_plane_bootstrap: IMediaPlaneBootstrap | None = None,
    ) -> None:
        self._repository = repository or SessionRepository()
        self._invite_service = invite_service or InviteService()
        self._media_plane_bootstrap = (
            media_plane_bootstrap or MediasoupMediaPlaneBootstrap()
        )

    def create_session(
        self,
        *,
        host_display_name: str,
        layout: str = LayoutType.CONTAIN,
    ) -> SessionCreateResult:
        invite_token = self._invite_service.generate_token()
        session = self._repository.create(
            host_display_name=host_display_name.strip(),
            invite_token=invite_token,
            layout=layout,
        )

        try:
            session = self._media_plane_bootstrap.bootstrap(session)
        except MediasoupApiError as exc:
            session.delete()
            raise MediasoupConnectionError(str(exc)) from exc

        session.status = SessionStatus.ACTIVE
        self._repository.save(session)

        emit_event(
            events.SESSION_CREATED,
            {
                'session_id': str(session.id),
                'room_id': session.room_id,
                'host_display_name': session.host_display_name,
                'layout': session.layout,
            },
        )

        from apps.scenes.service import SceneService

        SceneService(self).ensure_default_scene(session)

        return SessionCreateResult(
            session=session,
            invite_url=self._invite_service.build_invite_url(session),
            mediasoup_ws_url=self._invite_service.build_mediasoup_ws_url(),
        )

    def get_session(self, session_id: uuid.UUID) -> StudioSession:
        session = self._repository.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(f'Session {session_id} not found')
        return session

    def update_layout(self, session_id: uuid.UUID, layout: str) -> StudioSession:
        session = self.get_session(session_id)
        self._assert_not_ended(session)
        session.layout = layout
        session = self._repository.save(session)

        from apps.scenes.service import SceneService

        SceneService(self).sync_active_scene_layout(session, layout)

        from apps.compositor.commands import ChangeLayoutCommand
        from apps.compositor.worker_manager import get_session_worker_manager
        from apps.graphics.state import snapshot_graphics_state

        worker_manager = get_session_worker_manager()
        if worker_manager.is_running(str(session_id)):
            worker_manager.send_command(
                ChangeLayoutCommand(
                    session_id=str(session_id),
                    layout=layout,
                    graphics_state=snapshot_graphics_state(session.graphics_config or {}),
                )
            )

        return session

    def update_tile_config(
        self,
        session_id: uuid.UUID,
        *,
        host_peer_id: str | None = None,
        tile_order_config: dict | None = None,
        hidden_source_ids: list[str] | None = None,
        _host_peer_id_provided: bool = False,
        _tile_order_config_provided: bool = False,
        _hidden_source_ids_provided: bool = False,
    ) -> StudioSession:
        session = self.get_session(session_id)
        self._assert_not_ended(session)

        update_fields: list[str] = []

        if _host_peer_id_provided:
            session.host_peer_id = host_peer_id
            update_fields.append('host_peer_id')

        if _tile_order_config_provided:
            session.tile_order_config = merge_tile_order_config(
                tile_order_config,
                existing=session.tile_order_config,
            )
            update_fields.append('tile_order_config')

        if _hidden_source_ids_provided:
            session.hidden_source_ids = sanitize_hidden_source_ids(hidden_source_ids)
            update_fields.append('hidden_source_ids')

        if not update_fields:
            return session

        session = self._repository.save(session)
        self._sync_tile_order_to_worker(session)
        return session

    def _sync_tile_order_to_worker(self, session: StudioSession) -> None:
        from apps.compositor.tile_order_sync import send_tile_order_command

        send_tile_order_command(session)

    def end_session(self, session_id: uuid.UUID) -> StudioSession:
        session = self.get_session(session_id)
        if session.status == SessionStatus.ENDED:
            return session

        from apps.recording.service import RecordingService
        from apps.sources.service import RtmpSourceService
        from apps.streaming.service import StreamingService

        StreamingService().stop_active_stream_if_any(session_id)
        RecordingService().stop_active_recording_if_any(session_id)
        RtmpSourceService().stop_active_sources_if_any(session_id)
        self._media_plane_bootstrap.teardown(session)
        session.end()
        session = self._repository.save(session)

        emit_event(
            events.SESSION_ENDED,
            {
                'session_id': str(session_id),
                'room_id': session.room_id,
            },
        )

        return session

    def validate_invite(
        self,
        session_id: uuid.UUID,
        invite_token: str,
    ) -> InviteValidationResult:
        session = self.get_session(session_id)
        self._assert_not_ended(session)

        if session.invite_token != invite_token:
            raise InvalidInviteTokenError('Invalid invite token')

        return InviteValidationResult(
            session_id=str(session.id),
            room_id=session.room_id,
            mediasoup_ws_url=self._invite_service.build_mediasoup_ws_url(),
            layout=session.layout,
            host_display_name=session.host_display_name,
        )

    @staticmethod
    def _assert_not_ended(session: StudioSession) -> None:
        if session.status == SessionStatus.ENDED:
            raise SessionEndedError('Session has ended')
