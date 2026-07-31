"""Scene lifecycle: CRUD, activation, and active-scene sync."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from apps.compositor.commands import ChangeLayoutCommand, StartCountdownCommand, StopCountdownCommand
from apps.compositor.tile_order_sync import send_tile_order_command
from apps.compositor.worker_manager import get_session_worker_manager
from apps.graphics.state import empty_graphics_state, snapshot_graphics_state
from apps.compositor.tile_order import merge_sources_config
from apps.scenes.constants import (
    DEFAULT_BACKGROUND_MUSIC_CONFIG,
    DEFAULT_DEVICES_CONFIG,
    DEFAULT_SOURCES_CONFIG,
)
from apps.scenes.exceptions import (
    ActiveSceneDeleteError,
    CountdownAlreadyActiveError,
    InvalidCountdownTargetError,
    SceneNotFoundError,
)
from apps.scenes.models import SceneType, StudioScene
from apps.sessions.exceptions import SessionEndedError
from apps.sessions.models import LayoutType, StudioSession
from apps.sessions.services.session_service import SessionService

logger = logging.getLogger(__name__)

_countdown_timers: dict[uuid.UUID, threading.Timer] = {}
_countdown_timers_lock = threading.Lock()


class SceneService:
    def __init__(self, session_service: SessionService | None = None) -> None:
        self._sessions = session_service or SessionService()

    def list_scenes(self, session_id: uuid.UUID) -> list[StudioScene]:
        session = self._sessions.get_session(session_id)
        self._ensure_default_scene(session)
        return list(
            session.scenes.select_related('countdown_target_scene').order_by(
                'sort_order', 'created_at'
            )
        )

    def get_scene(self, session_id: uuid.UUID, scene_id: uuid.UUID) -> StudioScene:
        session = self._sessions.get_session(session_id)
        scene = session.scenes.filter(id=scene_id).first()
        if scene is None:
            raise SceneNotFoundError(f'Scene {scene_id} not found')
        return scene

    def create_scene(
        self,
        session_id: uuid.UUID,
        *,
        scene_type: str,
        devices: dict[str, Any] | None = None,
        layout: str | None = None,
        graphics_config: dict[str, Any] | None = None,
        duration_seconds: int | None = None,
        target_scene_id: uuid.UUID | None = None,
    ) -> StudioScene:
        session = self._sessions.get_session(session_id)
        self._sessions._assert_not_ended(session)
        self._ensure_default_scene(session)

        if scene_type == SceneType.COUNTDOWN:
            return self._create_countdown_scene(
                session,
                duration_seconds=duration_seconds,
                target_scene_id=target_scene_id,
            )

        return self._create_camera_scene(
            session,
            devices=devices,
            layout=layout,
            graphics_config=graphics_config,
        )

    def update_scene(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
        *,
        name: str | None = None,
        layout: str | None = None,
        graphics_config: dict[str, Any] | None = None,
        devices: dict[str, Any] | None = None,
        sources_config: dict[str, Any] | None = None,
        background_music_config: dict[str, Any] | None = None,
    ) -> StudioScene:
        session = self._sessions.get_session(session_id)
        self._sessions._assert_not_ended(session)
        scene = self.get_scene(session_id, scene_id)

        if scene.scene_type != SceneType.CAMERA:
            if any(
                v is not None
                for v in (layout, graphics_config, devices, sources_config)
            ):
                raise ValueError(
                    'Only camera scenes support layout/graphics/devices/sources updates'
                )
            if name is not None:
                scene.name = name.strip()
                scene.save(update_fields=['name', 'updated_at'])
            return scene

        update_fields = ['updated_at']
        if name is not None:
            scene.name = name.strip()
            update_fields.append('name')
        if layout is not None:
            scene.layout = layout
            update_fields.append('layout')
        if graphics_config is not None:
            scene.graphics_config = snapshot_graphics_state(graphics_config)
            update_fields.append('graphics_config')
        if devices is not None:
            scene.devices_config = {**DEFAULT_DEVICES_CONFIG, **devices}
            update_fields.append('devices_config')
        if sources_config is not None:
            scene.sources_config = merge_sources_config(
                sources_config,
                existing=scene.sources_config,
            )
            update_fields.append('sources_config')
        if background_music_config is not None:
            scene.background_music_config = background_music_config
            update_fields.append('background_music_config')

        scene.save(update_fields=update_fields)

        if session.active_scene_id == scene.id:
            self._apply_camera_scene_to_session(session, scene)
            session.save(update_fields=['layout', 'graphics_config'])
            self._sync_compositor_scene(session, scene)

        return scene

    def delete_scene(self, session_id: uuid.UUID, scene_id: uuid.UUID) -> None:
        session = self._sessions.get_session(session_id)
        self._sessions._assert_not_ended(session)
        scene = self.get_scene(session_id, scene_id)

        if session.active_scene_id == scene.id:
            raise ActiveSceneDeleteError('Cannot delete the active scene')

        scene.delete()

    def activate_scene(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
    ) -> tuple[StudioScene, str, dict[str, Any] | None]:
        session = self._sessions.get_session(session_id)
        self._sessions._assert_not_ended(session)
        scene = self.get_scene(session_id, scene_id)

        if scene.scene_type == SceneType.COUNTDOWN:
            countdown_state = self.start_countdown(session_id, scene_id)
            return scene, 'countdown', countdown_state

        self._apply_camera_scene_to_session(session, scene)
        session.active_scene = scene
        session.save(update_fields=['layout', 'graphics_config', 'active_scene_id'])

        self._sync_compositor_scene(session, scene)
        return scene, 'camera', None

    def start_countdown(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
    ) -> dict[str, Any]:
        session = self._sessions.get_session(session_id)
        self._sessions._assert_not_ended(session)
        scene = self.get_scene(session_id, scene_id)

        if scene.scene_type != SceneType.COUNTDOWN:
            raise InvalidCountdownTargetError('Scene is not a countdown scene')

        state = session.countdown_state or {}
        if state.get('active'):
            raise CountdownAlreadyActiveError('A countdown is already running')

        target = scene.countdown_target_scene
        if target is None or target.scene_type != SceneType.CAMERA:
            raise InvalidCountdownTargetError('Countdown target must be a camera scene')

        duration = scene.countdown_duration_seconds
        if duration is None or duration < 1:
            raise InvalidCountdownTargetError('Countdown duration is invalid')

        started_at = datetime.now(timezone.utc)
        countdown_state = {
            'active': True,
            'started_at': started_at.isoformat(),
            'duration_seconds': duration,
            'target_scene_id': str(target.id),
            'source_scene_id': str(scene.id),
        }
        session.countdown_state = countdown_state
        session.save(update_fields=['countdown_state'])

        self._send_start_countdown_command(session, started_at.timestamp(), duration)
        self._schedule_countdown_completion(session.id, float(duration))
        return countdown_state

    def complete_countdown(self, session_id: uuid.UUID) -> StudioScene | None:
        session = self._sessions.get_session(session_id)
        state = session.countdown_state or {}
        if not state.get('active'):
            return None

        self._cancel_countdown_timer(session_id)

        target_id = state.get('target_scene_id')
        target = None
        if target_id:
            target = session.scenes.filter(id=target_id, scene_type=SceneType.CAMERA).first()

        session.countdown_state = None
        update_fields = ['countdown_state']

        if target is not None:
            self._apply_camera_scene_to_session(session, target)
            session.active_scene = target
            update_fields.extend(['layout', 'graphics_config', 'active_scene_id'])

        session.save(update_fields=update_fields)
        self._send_stop_countdown_command(session)

        if target is not None:
            self._sync_compositor_scene(session, target)
        return target

    def sync_active_scene_layout(self, session: StudioSession, layout: str) -> None:
        if session.active_scene_id is None:
            return
        scene = session.active_scene
        if scene is None or scene.scene_type != SceneType.CAMERA:
            return
        scene.layout = layout
        scene.save(update_fields=['layout', 'updated_at'])

    def sync_active_scene_graphics(
        self,
        session: StudioSession,
        graphics_config: dict[str, Any],
    ) -> None:
        if session.active_scene_id is None:
            return
        scene = session.active_scene
        if scene is None or scene.scene_type != SceneType.CAMERA:
            return
        scene.graphics_config = snapshot_graphics_state(graphics_config)
        scene.save(update_fields=['graphics_config', 'updated_at'])

    def ensure_default_scene(self, session: StudioSession) -> StudioScene:
        return self._ensure_default_scene(session)

    def _ensure_default_scene(self, session: StudioSession) -> StudioScene:
        existing = session.scenes.filter(scene_type=SceneType.CAMERA).first()
        if existing is not None:
            if session.active_scene_id is None:
                session.active_scene = existing
                session.save(update_fields=['active_scene_id'])
            return existing

        scene = StudioScene.objects.create(
            session=session,
            name='Scene 1',
            scene_type=SceneType.CAMERA,
            sort_order=0,
            layout=session.layout or LayoutType.CONTAIN,
            graphics_config=snapshot_graphics_state(session.graphics_config or empty_graphics_state()),
            devices_config=dict(DEFAULT_DEVICES_CONFIG),
            sources_config=dict(DEFAULT_SOURCES_CONFIG),
            background_music_config=dict(DEFAULT_BACKGROUND_MUSIC_CONFIG),
        )
        session.active_scene = scene
        session.save(update_fields=['active_scene_id'])
        return scene

    def _create_camera_scene(
        self,
        session: StudioSession,
        *,
        devices: dict[str, Any] | None = None,
        layout: str | None = None,
        graphics_config: dict[str, Any] | None = None,
    ) -> StudioScene:
        camera_count = session.scenes.filter(scene_type=SceneType.CAMERA).count()
        max_order = (
            session.scenes.order_by('-sort_order').values_list('sort_order', flat=True).first()
        )
        sort_order = (max_order + 1) if max_order is not None else 0

        scene = StudioScene.objects.create(
            session=session,
            name=f'Scene {camera_count + 1}',
            scene_type=SceneType.CAMERA,
            sort_order=sort_order,
            layout=layout or session.layout or LayoutType.CONTAIN,
            graphics_config=snapshot_graphics_state(
                graphics_config
                if graphics_config is not None
                else session.graphics_config or empty_graphics_state()
            ),
            devices_config={**DEFAULT_DEVICES_CONFIG, **(devices or {})},
            sources_config=dict(DEFAULT_SOURCES_CONFIG),
            background_music_config=dict(DEFAULT_BACKGROUND_MUSIC_CONFIG),
        )
        return scene

    def _create_countdown_scene(
        self,
        session: StudioSession,
        *,
        duration_seconds: int | None,
        target_scene_id: uuid.UUID | None,
    ) -> StudioScene:
        if duration_seconds is None or target_scene_id is None:
            raise InvalidCountdownTargetError('duration_seconds and target_scene_id are required')

        target = session.scenes.filter(id=target_scene_id).first()
        if target is None or target.scene_type != SceneType.CAMERA:
            raise InvalidCountdownTargetError('Target must be an existing camera scene')

        max_order = (
            session.scenes.order_by('-sort_order').values_list('sort_order', flat=True).first()
        )
        sort_order = (max_order + 1) if max_order is not None else 0

        return StudioScene.objects.create(
            session=session,
            name=f'Countdown → {target.name}',
            scene_type=SceneType.COUNTDOWN,
            sort_order=sort_order,
            countdown_duration_seconds=duration_seconds,
            countdown_target_scene=target,
            devices_config=dict(DEFAULT_DEVICES_CONFIG),
            sources_config=dict(DEFAULT_SOURCES_CONFIG),
            background_music_config=dict(DEFAULT_BACKGROUND_MUSIC_CONFIG),
        )

    def _apply_camera_scene_to_session(
        self,
        session: StudioSession,
        scene: StudioScene,
    ) -> None:
        session.layout = scene.layout or LayoutType.CONTAIN
        session.graphics_config = snapshot_graphics_state(scene.graphics_config or empty_graphics_state())

    def _sync_compositor_scene(
        self,
        session: StudioSession,
        scene: StudioScene,
    ) -> None:
        send_tile_order_command(session, scene=scene)
        self._send_layout_command(session, scene.layout, scene.graphics_config)

    def _send_layout_command(
        self,
        session: StudioSession,
        layout: str,
        graphics_config: dict[str, Any],
    ) -> None:
        worker_manager = get_session_worker_manager()
        if not worker_manager.is_running(str(session.id)):
            logger.info(
                'Scene activated for session %s but session worker is not running',
                session.id,
            )
            return

        worker_manager.send_command(
            ChangeLayoutCommand(
                session_id=str(session.id),
                layout=layout,
                graphics_state=snapshot_graphics_state(graphics_config or {}),
            )
        )

    def _send_start_countdown_command(
        self,
        session: StudioSession,
        started_at_epoch: float,
        duration_seconds: int,
    ) -> None:
        worker_manager = get_session_worker_manager()
        if not worker_manager.is_running(str(session.id)):
            logger.info(
                'Countdown started for session %s but session worker is not running',
                session.id,
            )
            return

        worker_manager.send_command(
            StartCountdownCommand(
                session_id=str(session.id),
                started_at_epoch=started_at_epoch,
                duration_seconds=duration_seconds,
            )
        )

    def _send_stop_countdown_command(self, session: StudioSession) -> None:
        worker_manager = get_session_worker_manager()
        if not worker_manager.is_running(str(session.id)):
            return

        worker_manager.send_command(
            StopCountdownCommand(session_id=str(session.id))
        )

    def _schedule_countdown_completion(self, session_id: uuid.UUID, delay_seconds: float) -> None:
        def _complete() -> None:
            try:
                self.complete_countdown(session_id)
            except SessionEndedError:
                logger.info('Countdown completion skipped — session %s ended', session_id)
            except Exception:
                logger.exception('Failed to complete countdown for session %s', session_id)

        timer = threading.Timer(max(0.0, delay_seconds), _complete)
        timer.daemon = True
        with _countdown_timers_lock:
            existing = _countdown_timers.pop(session_id, None)
            if existing is not None:
                existing.cancel()
            _countdown_timers[session_id] = timer
        timer.start()

    def _cancel_countdown_timer(self, session_id: uuid.UUID) -> None:
        with _countdown_timers_lock:
            timer = _countdown_timers.pop(session_id, None)
        if timer is not None:
            timer.cancel()
