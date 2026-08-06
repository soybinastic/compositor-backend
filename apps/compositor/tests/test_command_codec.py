import uuid
from pathlib import Path

from django.test import TestCase

from apps.compositor.commands import (
    AddRtmpSourceCommand,
    ChangeLayoutCommand,
    CommandResult,
    CommandType,
    GetStatusCommand,
    RemoveRtmpSourceCommand,
    SetTileOrderCommand,
    StartRecordingCommand,
    StartCountdownCommand,
    StartStreamCommand,
    StopCountdownCommand,
    StopRecordingCommand,
    StopStreamCommand,
    SyncProducersCommand,
    UpdateGraphicsCommand,
)
from apps.compositor.session_ingest_manager import SessionIngestStatus
from apps.compositor.worker_manager.command_codec import (
    decode_command,
    decode_result,
    decode_session_ingest_status,
    encode_command,
    encode_result,
    encode_session_ingest_status,
)


class CommandCodecTests(TestCase):
    def test_change_layout_roundtrip(self):
        command = ChangeLayoutCommand(
            session_id='session-1',
            layout='GRID',
            graphics_state={'background': None},
        )
        restored = decode_command(encode_command(command))
        self.assertEqual(restored.session_id, command.session_id)
        self.assertEqual(restored.layout, 'GRID')
        self.assertEqual(restored.graphics_state, {'background': None})

    def test_start_recording_roundtrip_path(self):
        command = StartRecordingCommand(
            session_id='session-1',
            file_path=Path('/tmp/recording.mp4'),
        )
        restored = decode_command(encode_command(command))
        self.assertEqual(restored.file_path, Path('/tmp/recording.mp4'))

    def test_start_stream_roundtrip_optional_output_dir(self):
        command = StartStreamCommand(
            session_id='session-1',
            destination_type='rtmp',
            destination_url='rtmp://example/live',
            destination_urls=['rtmp://example/live', 'rtmp://other/live'],
            output_dir=Path('/tmp/hls'),
        )
        restored = decode_command(encode_command(command))
        self.assertEqual(restored.output_dir, Path('/tmp/hls'))
        self.assertEqual(
            restored.destination_urls,
            ['rtmp://example/live', 'rtmp://other/live'],
        )

    def test_sync_producers_roundtrip(self):
        peers = [{'peerId': 'guest-1', 'producers': []}]
        joined = [{'peerId': 'guest-1', 'displayName': 'Guest'}]
        command = SyncProducersCommand(
            session_id='session-1',
            peer_producers_infos=peers,
            joined_peers=joined,
        )
        restored = decode_command(encode_command(command))
        self.assertEqual(restored.peer_producers_infos, peers)
        self.assertEqual(restored.joined_peers, joined)

    def test_set_tile_order_roundtrip(self):
        command = SetTileOrderCommand(
            session_id='session-1',
            host_peer_id='host-a',
            slot_assignments={'0': 'host-a', '1': 'guest-b'},
            hidden_source_ids=['guest-hidden'],
        )
        restored = decode_command(encode_command(command))
        self.assertEqual(restored.host_peer_id, 'host-a')
        self.assertEqual(restored.slot_assignments, {'0': 'host-a', '1': 'guest-b'})
        self.assertEqual(restored.hidden_source_ids, ['guest-hidden'])

    def test_session_ingest_status_roundtrip(self):
        status = SessionIngestStatus(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-1',
            layout='CONTAIN',
            joined=True,
            composited_frames=12,
            canvas_width=1920,
            canvas_height=1080,
            host_peer_id='host-1',
            recording_active=False,
            recording_file_path=None,
            streaming_active=False,
            streaming_destination_type=None,
            streaming_destination_url=None,
            streaming_destination_urls=[],
            video_backend='cpu',
            requested_video_backend='cpu',
        )
        encoded = encode_session_ingest_status(status)
        restored = decode_session_ingest_status(encoded)
        self.assertEqual(restored.session_id, status.session_id)
        self.assertEqual(restored.composited_frames, 12)

    def test_command_result_with_status_roundtrip(self):
        status = SessionIngestStatus(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-1',
            layout='CONTAIN',
            joined=False,
            composited_frames=0,
            canvas_width=1920,
            canvas_height=1080,
            host_peer_id=None,
            recording_active=False,
            recording_file_path=None,
            streaming_active=False,
            streaming_destination_type=None,
            streaming_destination_url=None,
            streaming_destination_urls=[],
            video_backend='cpu',
            requested_video_backend='cpu',
        )
        result = CommandResult.ok(str(uuid.uuid4()), data=status)
        restored = decode_result(encode_result(result))
        self.assertTrue(restored.success)
        self.assertIsInstance(restored.data, SessionIngestStatus)
        self.assertEqual(restored.data.layout, 'CONTAIN')

    def test_all_command_types_registered(self):
        for command_type in CommandType:
            command = _sample_command(command_type)
            payload = encode_command(command)
            self.assertEqual(payload['command_type'], command_type.value)
            decode_command(payload)


def _sample_command(command_type: CommandType):
    session_id = 'session-1'
    if command_type == CommandType.CHANGE_LAYOUT:
        return ChangeLayoutCommand(session_id=session_id, layout='CONTAIN')
    if command_type == CommandType.UPDATE_GRAPHICS:
        return UpdateGraphicsCommand(session_id=session_id, graphics_state={'background': None})
    if command_type == CommandType.GET_STATUS:
        return GetStatusCommand(session_id=session_id)
    if command_type == CommandType.START_RECORDING:
        return StartRecordingCommand(session_id=session_id, file_path=Path('/tmp/a.mp4'))
    if command_type == CommandType.STOP_RECORDING:
        return StopRecordingCommand(session_id=session_id)
    if command_type == CommandType.START_STREAM:
        return StartStreamCommand(
            session_id=session_id,
            destination_type='rtmp',
            destination_url='rtmp://example/live',
        )
    if command_type == CommandType.STOP_STREAM:
        return StopStreamCommand(session_id=session_id)
    if command_type == CommandType.ADD_RTMP_SOURCE:
        return AddRtmpSourceCommand(
            session_id=session_id,
            source_id='src-1',
            url='rtmp://example/live',
        )
    if command_type == CommandType.REMOVE_RTMP_SOURCE:
        return RemoveRtmpSourceCommand(session_id=session_id, source_id='src-1')
    if command_type == CommandType.SYNC_PRODUCERS:
        return SyncProducersCommand(session_id=session_id, peer_producers_infos=[])
    if command_type == CommandType.START_COUNTDOWN:
        return StartCountdownCommand(
            session_id=session_id,
            started_at_epoch=1_700_000_000.0,
            duration_seconds=30,
        )
    if command_type == CommandType.STOP_COUNTDOWN:
        return StopCountdownCommand(session_id=session_id)
    if command_type == CommandType.SET_TILE_ORDER:
        return SetTileOrderCommand(
            session_id=session_id,
            host_peer_id='host-a',
            slot_assignments={'0': 'host-a'},
            hidden_source_ids=['guest-x'],
        )
    raise AssertionError(f'Unhandled command type: {command_type}')
