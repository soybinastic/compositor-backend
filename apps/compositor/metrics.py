"""Runtime metrics for active compositor sessions."""

from __future__ import annotations

import logging
from typing import Any

from apps.compositor.session_producer_poller import get_poller_registry
from apps.compositor.worker_manager import get_session_worker_manager
from apps.recording.models import RecordingStatus, SessionRecording
from apps.streaming.models import StreamStatus, SessionStream

logger = logging.getLogger(__name__)


def collect_metrics() -> dict[str, Any]:
    worker_manager = get_session_worker_manager()
    session_ids = worker_manager.list_running_session_ids()

    active_recordings = SessionRecording.objects.filter(
        status=RecordingStatus.RECORDING,
    ).count()
    active_streams = SessionStream.objects.filter(status=StreamStatus.LIVE).count()

    participant_count = 0
    recording_pipelines = 0
    streaming_pipelines = 0
    composited_frames = 0

    for session_id in session_ids:
        try:
            status = worker_manager.get_status(session_id)
        except Exception:
            logger.exception('Failed to collect status for session %s', session_id)
            continue
        if status is None:
            continue
        participant_count += len(status.participants)
        composited_frames += status.composited_frames
        if status.recording_active:
            recording_pipelines += 1
        if status.streaming_active:
            streaming_pipelines += 1

    active_pollers = get_poller_registry().count()

    return {
        'active_sessions': len(session_ids),
        'active_participants': participant_count,
        'active_recordings': active_recordings,
        'active_streams': active_streams,
        'recording_pipelines': recording_pipelines,
        'streaming_pipelines': streaming_pipelines,
        'composited_frames_total': composited_frames,
        'active_producer_pollers': active_pollers,
        # Backward-compatible alias (global watcher removed in Step 5).
        'producer_watcher_running': active_pollers > 0,
    }
