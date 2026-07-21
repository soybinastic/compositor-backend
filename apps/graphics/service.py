"""Graphics service: merge state, persist, apply to running pipeline."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from apps.graphics.constants import (
    LAYER_BANNER,
    LAYER_CHAT,
    LAYER_TICKER,
)
from apps.graphics.state import empty_graphics_state, merge_graphics_state, snapshot_graphics_state
from apps.graphics.visibility import banner_preserve_on_missing_flag
from apps.sessions.models import StudioSession
from apps.sessions.services.session_service import SessionService

logger = logging.getLogger(__name__)


class GraphicsService:
    def __init__(self, session_service: SessionService | None = None) -> None:
        self._sessions = session_service or SessionService()

    def get_graphics(self, session_id: uuid.UUID) -> dict[str, Any]:
        session = self._sessions.get_session(session_id)
        return snapshot_graphics_state(session.graphics_config or {})

    def update_bulk(self, session_id: uuid.UUID, partial: dict[str, Any]) -> dict[str, Any]:
        return self._update(session_id, partial)

    def update_layer(
        self,
        session_id: uuid.UUID,
        layer: str,
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self._update(session_id, {layer: config})

    def update_banner_ticker(
        self,
        session_id: uuid.UUID,
        *,
        banner: dict[str, Any] | None = None,
        ticker: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        partial: dict[str, Any] = {}
        if banner is not None:
            partial[LAYER_BANNER] = banner
        if ticker is not None:
            partial[LAYER_TICKER] = ticker
        return self._update(session_id, partial)

    def update_chat(
        self,
        session_id: uuid.UUID,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        return self._update(session_id, {LAYER_CHAT: config})

    def _update(self, session_id: uuid.UUID, partial: dict[str, Any]) -> dict[str, Any]:
        session = self._sessions.get_session(session_id)
        self._sessions._assert_not_ended(session)

        current = snapshot_graphics_state(session.graphics_config or {})

        if LAYER_BANNER in partial:
            partial = dict(partial)
            partial[LAYER_BANNER] = banner_preserve_on_missing_flag(
                partial[LAYER_BANNER],
                current.get(LAYER_BANNER),
            )

        merged = merge_graphics_state(current, partial)
        session.graphics_config = merged
        session.save(update_fields=['graphics_config'])

        self._apply_to_pipeline(session, merged, layout_only=False)
        return snapshot_graphics_state(merged)

    def apply_layout_only(self, session: StudioSession) -> None:
        """Re-evaluate background visibility after a layout change."""
        state = snapshot_graphics_state(session.graphics_config or {})
        self._apply_to_pipeline(session, state, layout_only=True)

    def restore_on_ingest_start(self, session: StudioSession) -> None:
        state = snapshot_graphics_state(session.graphics_config or empty_graphics_state())
        if not any(state.values()):
            return
        self._apply_to_pipeline(session, state, layout_only=False)

    def _apply_to_pipeline(
        self,
        session: StudioSession,
        state: dict[str, Any],
        *,
        layout_only: bool,
    ) -> None:
        from apps.compositor.registry import get as get_ingest_manager

        ingest_manager = get_ingest_manager(str(session.id))
        if ingest_manager is None:
            # Persist-only is OK for GET before ingest starts; writes still succeed.
            # Applying to canvas requires a live pipeline — soft-skip rather than 503
            # so clients can stage graphics before producers join.
            logger.info(
                'Graphics saved for session %s but ingest manager is not running',
                session.id,
            )
            return

        ingest_manager.apply_graphics(state, layout_only=layout_only)
