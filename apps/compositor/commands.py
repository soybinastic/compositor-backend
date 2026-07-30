"""Typed session commands for the compositor media plane."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class CommandType(str, Enum):
    """Command identifiers exchanged between API and session workers."""

    CHANGE_LAYOUT = 'ChangeLayout'
    UPDATE_GRAPHICS = 'UpdateGraphics'
    START_RECORDING = 'StartRecording'
    STOP_RECORDING = 'StopRecording'
    START_STREAM = 'StartStream'
    STOP_STREAM = 'StopStream'
    ADD_RTMP_SOURCE = 'AddRtmpSource'
    REMOVE_RTMP_SOURCE = 'RemoveRtmpSource'
    GET_STATUS = 'GetStatus'
    SYNC_PRODUCERS = 'SyncProducers'


@dataclass(frozen=True, kw_only=True)
class SessionCommand:
    """Base command targeting one studio session."""

    session_id: str
    command_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def command_type(self) -> CommandType:
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True)
class ChangeLayoutCommand(SessionCommand):
    layout: str
    graphics_state: dict[str, Any] | None = None

    @property
    def command_type(self) -> CommandType:
        return CommandType.CHANGE_LAYOUT


@dataclass(frozen=True, kw_only=True)
class UpdateGraphicsCommand(SessionCommand):
    graphics_state: dict[str, Any]
    layout_only: bool = False

    @property
    def command_type(self) -> CommandType:
        return CommandType.UPDATE_GRAPHICS


@dataclass(frozen=True, kw_only=True)
class StartRecordingCommand(SessionCommand):
    file_path: Path

    @property
    def command_type(self) -> CommandType:
        return CommandType.START_RECORDING


@dataclass(frozen=True, kw_only=True)
class StopRecordingCommand(SessionCommand):
    @property
    def command_type(self) -> CommandType:
        return CommandType.STOP_RECORDING


@dataclass(frozen=True, kw_only=True)
class StartStreamCommand(SessionCommand):
    destination_type: str
    destination_url: str
    output_dir: Path | None = None

    @property
    def command_type(self) -> CommandType:
        return CommandType.START_STREAM


@dataclass(frozen=True, kw_only=True)
class StopStreamCommand(SessionCommand):
    @property
    def command_type(self) -> CommandType:
        return CommandType.STOP_STREAM


@dataclass(frozen=True, kw_only=True)
class AddRtmpSourceCommand(SessionCommand):
    source_id: str
    url: str
    display_name: str = ''

    @property
    def command_type(self) -> CommandType:
        return CommandType.ADD_RTMP_SOURCE


@dataclass(frozen=True, kw_only=True)
class RemoveRtmpSourceCommand(SessionCommand):
    source_id: str

    @property
    def command_type(self) -> CommandType:
        return CommandType.REMOVE_RTMP_SOURCE


@dataclass(frozen=True, kw_only=True)
class GetStatusCommand(SessionCommand):
    @property
    def command_type(self) -> CommandType:
        return CommandType.GET_STATUS


@dataclass(frozen=True, kw_only=True)
class SyncProducersCommand(SessionCommand):
    peer_producers_infos: list[dict[str, Any]]

    @property
    def command_type(self) -> CommandType:
        return CommandType.SYNC_PRODUCERS


@dataclass
class CommandResult:
    """Outcome of a synchronous command execution."""

    command_id: str
    success: bool
    data: Any = None
    error: str | None = None

    @classmethod
    def ok(cls, command_id: str, data: Any = None) -> CommandResult:
        return cls(command_id=command_id, success=True, data=data)

    @classmethod
    def fail(cls, command_id: str, error: str) -> CommandResult:
        return cls(command_id=command_id, success=False, error=error)
