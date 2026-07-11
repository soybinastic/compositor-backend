"""Runtime metrics for active compositor sessions."""

from __future__ import annotations

from typing import Any

from apps.compositor.producer_watcher import ProducerWatcher
from apps.compositor.registry import all_managers
from apps.recording.models import RecordingStatus, SessionRecording
from apps.streaming.models import StreamStatus, SessionStream


def collect_metrics() -> dict[str, Any]:
    managers = all_managers()
    watcher = ProducerWatcher.instance()

    active_recordings = SessionRecording.objects.filter(
        status=RecordingStatus.RECORDING,
    ).count()
    active_streams = SessionStream.objects.filter(status=StreamStatus.LIVE).count()

    participant_count = 0
    recording_pipelines = 0
    streaming_pipelines = 0
    composited_frames = 0

    for manager in managers:
        status = manager.get_status()
        participant_count += len(status.participants)
        composited_frames += status.composited_frames
        if status.recording_active:
            recording_pipelines += 1
        if status.streaming_active:
            streaming_pipelines += 1

    return {
        'active_sessions': len(managers),
        'active_participants': participant_count,
        'active_recordings': active_recordings,
        'active_streams': active_streams,
        'recording_pipelines': recording_pipelines,
        'streaming_pipelines': streaming_pipelines,
        'composited_frames_total': composited_frames,
        'producer_watcher_running': bool(
            watcher._thread and watcher._thread.is_alive()
        ),
    }
