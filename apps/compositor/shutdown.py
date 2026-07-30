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
    """Stop streams, recordings, session workers, and background workers."""
    from apps.compositor.session_producer_poller import get_poller_registry
    from apps.compositor.worker_manager import get_session_worker_manager
    from apps.recording.service import RecordingService
    from apps.streaming.service import StreamingService
    from core.webhooks import flush_pending, stop_worker

    logger.info('Graceful shutdown started')

    worker_manager = get_session_worker_manager()
    session_ids = worker_manager.list_running_session_ids()
    recording_service = RecordingService()
    streaming_service = StreamingService()

    for session_id in session_ids:
        try:
            streaming_service.stop_active_stream_if_any(uuid.UUID(session_id))
        except Exception:
            logger.exception(
                'Failed to stop active stream during shutdown for %s',
                session_id,
            )
        try:
            recording_service.stop_active_recording_if_any(uuid.UUID(session_id))
        except Exception:
            logger.exception(
                'Failed to stop active recording during shutdown for %s',
                session_id,
            )

    for session_id in session_ids:
        try:
            worker_manager.destroy_session(session_id)
        except Exception:
            logger.exception(
                'Failed to destroy session worker for %s',
                session_id,
            )

    get_poller_registry().shutdown_all()
    from apps.compositor.worker_manager.worker_event_consumer import WorkerEventConsumer

    WorkerEventConsumer.instance().stop()
    flush_pending(timeout_sec=getattr(settings, 'GRACEFUL_SHUTDOWN_TIMEOUT_SEC', 30) / 2)
    stop_worker()

    logger.info('Graceful shutdown complete')
