"""Dispatch worker-originated lifecycle events on the API process."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from core import events
from core.webhooks import emit_event

logger = logging.getLogger(__name__)


def dispatch_worker_event(event_type: str, payload: dict[str, Any]) -> None:
    """
    Handle an event emitted by a session worker.

    Webhooks always go through emit_event. Selected events also update ORM state
    owned by the API control plane.
    """
    emit_event(event_type, payload)

    if event_type == events.STREAM_FAILED:
        _mark_stream_failed(payload)
    elif event_type == events.STREAM_DESTINATION_FAILED:
        _mark_stream_destination_failed(payload)


def _mark_stream_failed(payload: dict[str, Any]) -> None:
    session_id = payload.get('session_id')
    if not session_id:
        logger.warning('STREAM_FAILED event missing session_id: %s', payload)
        return

    error = payload.get('error', '')
    try:
        from apps.streaming.service import StreamingService

        StreamingService().mark_active_stream_failed(uuid.UUID(str(session_id)), str(error))
    except Exception:
        logger.exception(
            'Failed to mark stream failed for session %s',
            session_id,
        )


def _mark_stream_destination_failed(payload: dict[str, Any]) -> None:
    session_id = payload.get('session_id')
    destination_url = payload.get('destination_url')
    if not session_id or not destination_url:
        logger.warning(
            'STREAM_DESTINATION_FAILED event missing fields: %s',
            payload,
        )
        return

    error = payload.get('error', '')
    try:
        from apps.streaming.service import StreamingService

        StreamingService().mark_stream_destination_failed(
            uuid.UUID(str(session_id)),
            str(destination_url),
            str(error),
        )
    except Exception:
        logger.exception(
            'Failed to mark stream destination failed for session %s',
            session_id,
        )
