"""Session Source registry service (SOLID facade over handlers)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.utils import timezone

from apps.compositor.tile_order import assignments_from_scene_items, merge_sources_config
from apps.scenes.models import StudioScene
from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError
from apps.sessions.models import SessionStatus, StudioSession
from apps.sessions.repositories.session_repository import SessionRepository
from apps.sources.exceptions import (
    IngestManagerNotRunningError,
    SourceAlreadyAttachedError,
    SourceNotFoundError,
    SourceTypeNotImplementedError,
    UnsupportedSourceTypeError,
)
from apps.sources.handlers import (
    IMPLEMENTED_RUNTIME_TYPES,
    SUPPORTED_CREATE_TYPES,
    get_source_handler_factory,
)
from apps.sources.models import SessionSource, SourceState, SourceType


@dataclass(frozen=True)
class SourceResult:
    source_id: str
    session_id: uuid.UUID
    type: str
    name: str
    state: str
    volume: float
    muted: bool
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SourceService:
    """CRUD + lifecycle for session-scoped Sources."""

    def __init__(self, repository: SessionRepository | None = None) -> None:
        self._repository = repository or SessionRepository()
        self._handlers = get_source_handler_factory()

    def list_sources(self, session_id: uuid.UUID) -> list[SourceResult]:
        session = self._get_session(session_id)
        rows = SessionSource.objects.filter(session=session).order_by('created_at')
        return [self._to_result(row) for row in rows]

    def get_source(self, session_id: uuid.UUID, source_id: str) -> SourceResult:
        return self._to_result(self._get_row(session_id, source_id))

    def create_source(
        self,
        session_id: uuid.UUID,
        *,
        source_type: str,
        name: str = '',
        settings: dict[str, Any] | None = None,
        volume: float = 1.0,
        muted: bool = False,
        start: bool = True,
    ) -> SourceResult:
        session = self._get_active_session(session_id)
        if source_type not in SUPPORTED_CREATE_TYPES:
            raise UnsupportedSourceTypeError(f'Unsupported source type: {source_type}')

        settings = dict(settings or {})
        source_id = self._make_source_id(source_type)
        display_name = (name or self._default_name(source_type, settings)).strip()

        row = SessionSource.objects.create(
            session=session,
            source_id=source_id,
            type=source_type,
            name=display_name,
            state=SourceState.LOADING if start else SourceState.STOPPED,
            volume=max(0.0, min(1.0, float(volume))),
            muted=bool(muted),
            settings=settings,
        )

        if start and source_type in IMPLEMENTED_RUNTIME_TYPES:
            try:
                handler = self._handlers.get(source_type)
                handler.start(
                    source_id,
                    settings,
                    session_id=str(session_id),
                    display_name=display_name,
                )
                row.mark_state(SourceState.ACTIVE)
                row.save(update_fields=['state', 'stopped_at', 'updated_at'])
            except NotImplementedError as exc:
                row.mark_state(SourceState.STOPPED)
                row.save(update_fields=['state', 'stopped_at', 'updated_at'])
                raise SourceTypeNotImplementedError(str(exc)) from exc
            except Exception:
                row.mark_state(SourceState.STOPPED)
                row.save(update_fields=['state', 'stopped_at', 'updated_at'])
                raise
        elif start and source_type not in IMPLEMENTED_RUNTIME_TYPES:
            row.mark_state(SourceState.STOPPED)
            row.save(update_fields=['state', 'stopped_at', 'updated_at'])

        return self._to_result(row)

    def update_source(
        self,
        session_id: uuid.UUID,
        source_id: str,
        *,
        name: str | None = None,
        volume: float | None = None,
        muted: bool | None = None,
        settings: dict[str, Any] | None = None,
    ) -> SourceResult:
        row = self._get_row(session_id, source_id)
        update_fields = ['updated_at']
        if name is not None:
            row.name = name.strip() or row.name
            update_fields.append('name')
        if volume is not None:
            row.volume = max(0.0, min(1.0, float(volume)))
            update_fields.append('volume')
        if muted is not None:
            row.muted = bool(muted)
            update_fields.append('muted')
        if settings is not None:
            merged = dict(row.settings or {})
            merged.update(settings)
            row.settings = merged
            update_fields.append('settings')
        row.save(update_fields=update_fields)

        if row.type == SourceType.PRERECORDED and (
            volume is not None or muted is not None
        ):
            try:
                handler = self._handlers.get(row.type)
                if hasattr(handler, 'apply_playback'):
                    handler.apply_playback(
                        source_id,
                        session_id=str(session_id),
                        volume=row.volume,
                        muted=row.muted,
                    )
            except Exception:
                pass

        return self._to_result(row)

    def delete_source(self, session_id: uuid.UUID, source_id: str) -> None:
        row = self._get_row(session_id, source_id)
        try:
            handler = self._handlers.get(row.type)
            handler.stop(source_id, session_id=str(session_id))
        except Exception:
            pass
        self._detach_from_all_scenes(session_id, source_id)
        row.delete()

    def play(self, session_id: uuid.UUID, source_id: str) -> SourceResult:
        return self._playback_action(session_id, source_id, 'resume', SourceState.ACTIVE)

    def pause(self, session_id: uuid.UUID, source_id: str) -> SourceResult:
        return self._playback_action(session_id, source_id, 'pause', SourceState.PAUSED)

    def stop_playback(self, session_id: uuid.UUID, source_id: str) -> SourceResult:
        row = self._get_row(session_id, source_id)
        handler = self._handlers.get(row.type)
        handler.stop(source_id, session_id=str(session_id))
        row.mark_state(SourceState.STOPPED)
        row.save(update_fields=['state', 'stopped_at', 'updated_at'])
        return self._to_result(row)

    def seek(
        self,
        session_id: uuid.UUID,
        source_id: str,
        *,
        position_ms: float,
    ) -> SourceResult:
        row = self._get_row(session_id, source_id)
        handler = self._handlers.get(row.type)
        handler.seek(source_id, position_ms, session_id=str(session_id))
        return self._to_result(row)

    def attach_to_scene(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
        source_id: str,
        *,
        visible: bool = True,
    ) -> dict[str, Any]:
        self._get_row(session_id, source_id)
        scene = self._get_scene(session_id, scene_id)
        config = dict(scene.sources_config or {})
        items = list(config.get('items') or [])
        for item in items:
            if isinstance(item, dict) and item.get('sourceId') == source_id:
                raise SourceAlreadyAttachedError(
                    f'Source {source_id} is already attached to scene {scene_id}'
                )

        z_index = max((int(i.get('zIndex', 0)) for i in items if isinstance(i, dict)), default=-1) + 1
        item = {
            'id': f'item-{uuid.uuid4().hex[:12]}',
            'sceneId': str(scene_id),
            'sourceId': source_id,
            'visible': visible,
            'zIndex': z_index,
        }
        items.append(item)
        return self._save_scene_items(scene, items)

    def detach_from_scene(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
        source_id: str,
    ) -> dict[str, Any]:
        scene = self._get_scene(session_id, scene_id)
        config = dict(scene.sources_config or {})
        items = [
            item
            for item in (config.get('items') or [])
            if not (isinstance(item, dict) and item.get('sourceId') == source_id)
        ]
        # Re-pack zIndex
        items = self._normalize_z_index(items)
        return self._save_scene_items(scene, items)

    def set_item_visibility(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
        source_id: str,
        *,
        visible: bool,
    ) -> dict[str, Any]:
        scene = self._get_scene(session_id, scene_id)
        items = list((scene.sources_config or {}).get('items') or [])
        found = False
        for item in items:
            if isinstance(item, dict) and item.get('sourceId') == source_id:
                item['visible'] = visible
                found = True
                break
        if not found:
            raise SourceNotFoundError(f'Source {source_id} is not attached to scene')
        return self._save_scene_items(scene, items)

    def reorder_scene_items(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
        source_ids: list[str],
    ) -> dict[str, Any]:
        scene = self._get_scene(session_id, scene_id)
        items = list((scene.sources_config or {}).get('items') or [])
        by_source = {
            item['sourceId']: item
            for item in items
            if isinstance(item, dict) and item.get('sourceId')
        }
        ordered: list[dict] = []
        for index, source_id in enumerate(source_ids):
            item = by_source.pop(source_id, None)
            if item is None:
                continue
            item['zIndex'] = index
            ordered.append(item)
        for index, item in enumerate(by_source.values(), start=len(ordered)):
            item['zIndex'] = index
            ordered.append(item)
        return self._save_scene_items(scene, ordered)

    def _playback_action(
        self,
        session_id: uuid.UUID,
        source_id: str,
        method: str,
        state: str,
    ) -> SourceResult:
        row = self._get_row(session_id, source_id)
        handler = self._handlers.get(row.type)
        getattr(handler, method)(source_id, session_id=str(session_id))
        row.mark_state(state)
        row.save(update_fields=['state', 'stopped_at', 'updated_at'])
        return self._to_result(row)

    def _save_scene_items(self, scene: StudioScene, items: list[dict]) -> dict[str, Any]:
        config = merge_sources_config(
            {
                'version': 2,
                'items': items,
                'assignments': assignments_from_scene_items(items),
            },
            existing=scene.sources_config,
        )
        scene.sources_config = config
        scene.save(update_fields=['sources_config', 'updated_at'])
        return config

    def _detach_from_all_scenes(self, session_id: uuid.UUID, source_id: str) -> None:
        for scene in StudioScene.objects.filter(session_id=session_id):
            config = dict(scene.sources_config or {})
            items = list(config.get('items') or [])
            next_items = [
                item
                for item in items
                if not (isinstance(item, dict) and item.get('sourceId') == source_id)
            ]
            if len(next_items) != len(items):
                self._save_scene_items(scene, self._normalize_z_index(next_items))

    @staticmethod
    def _normalize_z_index(items: list[dict]) -> list[dict]:
        ordered = sorted(
            [item for item in items if isinstance(item, dict)],
            key=lambda item: int(item.get('zIndex', 0)),
        )
        for index, item in enumerate(ordered):
            item['zIndex'] = index
        return ordered

    def _get_row(self, session_id: uuid.UUID, source_id: str) -> SessionSource:
        session = self._get_session(session_id)
        row = SessionSource.objects.filter(session=session, source_id=source_id).first()
        if row is None:
            raise SourceNotFoundError(f'Source {source_id} not found')
        return row

    def _get_scene(self, session_id: uuid.UUID, scene_id: uuid.UUID) -> StudioScene:
        scene = StudioScene.objects.filter(session_id=session_id, id=scene_id).first()
        if scene is None:
            raise SourceNotFoundError(f'Scene {scene_id} not found')
        return scene

    def _get_session(self, session_id: uuid.UUID) -> StudioSession:
        session = self._repository.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(f'Session {session_id} not found')
        return session

    def _get_active_session(self, session_id: uuid.UUID) -> StudioSession:
        session = self._get_session(session_id)
        if session.status != SessionStatus.ACTIVE:
            raise SessionEndedError('Session is not active')
        return session

    @staticmethod
    def _make_source_id(source_type: str) -> str:
        return f'{source_type}-{uuid.uuid4().hex[:12]}'

    @staticmethod
    def _default_name(source_type: str, settings: dict[str, Any]) -> str:
        if source_type == SourceType.PRERECORDED:
            return str(settings.get('title') or 'Pre-recorded Video')
        if source_type == SourceType.CAMERA:
            return str(settings.get('deviceLabel') or settings.get('deviceId') or 'Camera')
        if source_type == SourceType.SCREEN:
            return 'Screen Share'
        return source_type.replace('_', ' ').title()

    @staticmethod
    def _to_result(row: SessionSource) -> SourceResult:
        return SourceResult(
            source_id=row.source_id,
            session_id=row.session_id,
            type=row.type,
            name=row.name,
            state=row.state,
            volume=row.volume,
            muted=row.muted,
            settings=dict(row.settings or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
