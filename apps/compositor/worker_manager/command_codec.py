"""Serialize session commands and results for Redis IPC."""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any

from apps.compositor.commands import (
    AddRtmpSourceCommand,
    ChangeLayoutCommand,
    CommandResult,
    CommandType,
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
from apps.compositor.session_ingest_manager import (
    ParticipantIngestStatus,
    RtmpSourceIngestStatus,
    SessionIngestStatus,
)

_COMMAND_TYPES: dict[CommandType, type[SessionCommand]] = {
    CommandType.CHANGE_LAYOUT: ChangeLayoutCommand,
    CommandType.UPDATE_GRAPHICS: UpdateGraphicsCommand,
    CommandType.START_RECORDING: StartRecordingCommand,
    CommandType.STOP_RECORDING: StopRecordingCommand,
    CommandType.START_STREAM: StartStreamCommand,
    CommandType.STOP_STREAM: StopStreamCommand,
    CommandType.ADD_RTMP_SOURCE: AddRtmpSourceCommand,
    CommandType.REMOVE_RTMP_SOURCE: RemoveRtmpSourceCommand,
    CommandType.GET_STATUS: GetStatusCommand,
    CommandType.SYNC_PRODUCERS: SyncProducersCommand,
}

_PATH_FIELDS = frozenset({'file_path', 'output_dir'})
_LIST_FIELDS = frozenset({'destination_urls'})


def encode_command(command: SessionCommand) -> dict[str, Any]:
    payload = _encode_value(asdict(command))
    payload['command_type'] = command.command_type.value
    return payload


def decode_command(payload: dict[str, Any]) -> SessionCommand:
    command_type = CommandType(payload['command_type'])
    command_cls = _COMMAND_TYPES[command_type]
    decoded: dict[str, Any] = {}
    for field in fields(command_cls):
        raw = payload.get(field.name)
        if field.name in _PATH_FIELDS:
            decoded[field.name] = Path(raw) if raw is not None else None
        elif field.name in _LIST_FIELDS:
            decoded[field.name] = list(raw) if raw is not None else None
        else:
            decoded[field.name] = raw
    return command_cls(**decoded)


def encode_result(result: CommandResult) -> dict[str, Any]:
    return {
        'command_id': result.command_id,
        'success': result.success,
        'data': _encode_value(result.data),
        'error': result.error,
    }


def decode_result(payload: dict[str, Any]) -> CommandResult:
    data = _decode_value(payload.get('data'))
    return CommandResult(
        command_id=payload['command_id'],
        success=payload['success'],
        data=data,
        error=payload.get('error'),
    )


def encode_session_ingest_status(status: SessionIngestStatus) -> dict[str, Any]:
    encoded = _encode_value(asdict(status))
    encoded['_type'] = 'SessionIngestStatus'
    return encoded


def decode_session_ingest_status(payload: dict[str, Any]) -> SessionIngestStatus:
    participants = [
        ParticipantIngestStatus(**item)
        for item in payload.get('participants', [])
    ]
    rtmp_sources = [
        RtmpSourceIngestStatus(**item)
        for item in payload.get('rtmp_sources', [])
    ]
    return SessionIngestStatus(
        session_id=payload['session_id'],
        room_id=payload['room_id'],
        compositor_peer_id=payload['compositor_peer_id'],
        layout=payload['layout'],
        joined=payload['joined'],
        composited_frames=payload['composited_frames'],
        canvas_width=payload['canvas_width'],
        canvas_height=payload['canvas_height'],
        host_peer_id=payload.get('host_peer_id'),
        recording_active=payload['recording_active'],
        recording_file_path=payload.get('recording_file_path'),
        streaming_active=payload['streaming_active'],
        streaming_destination_type=payload.get('streaming_destination_type'),
        streaming_destination_url=payload.get('streaming_destination_url'),
        streaming_destination_urls=payload.get('streaming_destination_urls') or [],
        video_backend=payload.get('video_backend'),
        requested_video_backend=payload['requested_video_backend'],
        participants=participants,
        rtmp_sources=rtmp_sources,
    )


def _encode_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        encoded = asdict(value)
        if isinstance(value, SessionIngestStatus):
            encoded['_type'] = 'SessionIngestStatus'
        return encoded
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_encode_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode_value(item) for key, item in value.items()}
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get('_type') == 'SessionIngestStatus':
            return decode_session_ingest_status(value)
        return {key: _decode_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    return value
