"""Execute typed session commands against a SessionIngestManager."""

from __future__ import annotations

from apps.compositor.commands import (
    AddRtmpSourceCommand,
    ChangeLayoutCommand,
    GetStatusCommand,
    RemoveRtmpSourceCommand,
    SessionCommand,
    StartRecordingCommand,
    StartStreamCommand,
    StopRecordingCommand,
    StopStreamCommand,
    SyncProducersCommand,
    UpdateGraphicsCommand,
)
from apps.compositor.session_ingest_manager import SessionIngestManager


def dispatch_command(ingest_manager: SessionIngestManager, command: SessionCommand):
    """Run one command on the session ingest manager (caller must own the thread)."""
    if isinstance(command, ChangeLayoutCommand):
        ingest_manager.set_layout(
            command.layout,
            graphics_state=command.graphics_state,
        )
        return None

    if isinstance(command, UpdateGraphicsCommand):
        ingest_manager.apply_graphics(
            command.graphics_state,
            layout_only=command.layout_only,
        )
        return None

    if isinstance(command, StartRecordingCommand):
        ingest_manager.start_recording(command.file_path)
        return None

    if isinstance(command, StopRecordingCommand):
        return ingest_manager.stop_recording()

    if isinstance(command, StartStreamCommand):
        ingest_manager.start_stream(
            destination_type=command.destination_type,
            destination_url=command.destination_url,
            output_dir=command.output_dir,
        )
        return None

    if isinstance(command, StopStreamCommand):
        ingest_manager.stop_stream()
        return None

    if isinstance(command, AddRtmpSourceCommand):
        ingest_manager.add_rtmp_source(
            source_id=command.source_id,
            url=command.url,
            display_name=command.display_name,
        )
        return None

    if isinstance(command, RemoveRtmpSourceCommand):
        ingest_manager.remove_rtmp_source(command.source_id)
        return None

    if isinstance(command, SyncProducersCommand):
        ingest_manager.sync_producers(command.peer_producers_infos)
        return None

    if isinstance(command, GetStatusCommand):
        return ingest_manager.get_status()

    raise TypeError(f'Unsupported command type: {type(command).__name__}')
