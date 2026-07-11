"""Graceful shutdown of compositor pipelines and background workers."""

from __future__ import annotations

import logging
import signal
import sys
import uuid

from django.conf import settings

logger = logging.getLogger(__name__)

_handlers_registered = False


def register_shutdown_handlers() -> None:
    """Register SIGTERM/SIGINT handlers once per process."""
    global _handlers_registered
    if _handlers_registered:
        return

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    _handlers_registered = True
    logger.info('Graceful shutdown handlers registered')


def _handle_signal(signum, _frame) -> None:
    signal_name = signal.Signals(signum).name
    logger.info('Received %s — initiating graceful shutdown', signal_name)
    graceful_shutdown()
    sys.exit(0)


def graceful_shutdown() -> None:
    """Stop streams, recordings, ingest pipelines, and background workers."""
    from apps.compositor.producer_watcher import ProducerWatcher
    from apps.compositor.registry import all_managers, clear_all
    from apps.recording.service import RecordingService
    from apps.streaming.service import StreamingService
    from core.webhooks import flush_pending, stop_worker

    logger.info('Graceful shutdown started')

    managers = all_managers()
    recording_service = RecordingService()
    streaming_service = StreamingService()

    for manager in managers:
        session_id = uuid.UUID(manager.session_id)
        try:
            streaming_service.stop_active_stream_if_any(session_id)
        except Exception:
            logger.exception(
                'Failed to stop active stream during shutdown for %s',
                manager.session_id,
            )
        try:
            recording_service.stop_active_recording_if_any(session_id)
        except Exception:
            logger.exception(
                'Failed to stop active recording during shutdown for %s',
                manager.session_id,
            )

    for manager in managers:
        try:
            manager.stop()
        except Exception:
            logger.exception(
                'Failed to stop ingest manager for %s',
                manager.session_id,
            )

    clear_all()
    ProducerWatcher.instance().stop()
    flush_pending(timeout_sec=getattr(settings, 'GRACEFUL_SHUTDOWN_TIMEOUT_SEC', 30) / 2)
    stop_worker()

    logger.info('Graceful shutdown complete')
