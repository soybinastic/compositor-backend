import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.recording.models import RecordingStatus, SessionRecording
from apps.sessions.models import StudioSession
from apps.streaming.exceptions import (
    IngestManagerNotRunningError,
    InvalidDestinationError,
    StreamAlreadyActiveError,
    StreamNotActiveError,
)
from apps.streaming.models import DestinationType, SessionStream, StreamStatus
from apps.streaming.service import StreamingService


class StreamingServiceTests(TestCase):
    def setUp(self):
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id='compositor-test',
        )
        self.service = StreamingService()

    @patch('apps.streaming.service.get_session_worker_manager')
    def test_start_rtmp_stream(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_get_manager.return_value = mock_manager

        result = self.service.start_stream(
            self.session.id,
            destination_type=DestinationType.RTMP,
            destination_url='rtmp://live.example.com/app/stream-key',
        )

        self.assertEqual(result.status, StreamStatus.LIVE)
        self.assertEqual(result.destination_type, DestinationType.RTMP)
        self.assertEqual(result.destination_urls, ['rtmp://live.example.com/app/stream-key'])
        self.assertEqual(len(result.destinations), 1)
        mock_manager.send_command.assert_called_once()
        self.assertEqual(SessionStream.objects.count(), 1)

    @patch('apps.streaming.service.get_session_worker_manager')
    def test_start_rtmp_stream_multiple_destinations(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_get_manager.return_value = mock_manager

        result = self.service.start_stream(
            self.session.id,
            destination_type=DestinationType.RTMP,
            destinations=[
                {'url': 'rtmp://live.twitch.tv/app/key-1', 'label': 'Twitch'},
                {'url': 'rtmp://a.rtmp.youtube.com/live2/key-2', 'label': 'YouTube'},
            ],
        )

        self.assertEqual(result.status, StreamStatus.LIVE)
        self.assertEqual(len(result.destination_urls), 2)
        self.assertEqual(len(result.destinations), 2)
        self.assertEqual(result.destinations[0].label, 'Twitch')
        command = mock_manager.send_command.call_args.args[0]
        self.assertEqual(
            command.destination_urls,
            [
                'rtmp://live.twitch.tv/app/key-1',
                'rtmp://a.rtmp.youtube.com/live2/key-2',
            ],
        )

    @patch('apps.streaming.service.get_session_worker_manager')
    @override_settings(STREAMING_HLS_DIR='/tmp/test-hls')
    def test_start_hls_stream(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_get_manager.return_value = mock_manager

        result = self.service.start_stream(
            self.session.id,
            destination_type=DestinationType.HLS,
        )

        self.assertEqual(result.status, StreamStatus.LIVE)
        self.assertEqual(result.destination_type, DestinationType.HLS)
        self.assertIn('playlist.m3u8', result.destination_url)
        mock_manager.send_command.assert_called_once()

    def test_start_rtmp_requires_valid_url(self):
        with self.assertRaises(InvalidDestinationError):
            self.service.start_stream(
                self.session.id,
                destination_type=DestinationType.RTMP,
                destination_url='http://not-rtmp.example/live',
            )

    @patch('apps.streaming.service.get_session_worker_manager')
    def test_mark_stream_destination_failed_keeps_session_live(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_get_manager.return_value = mock_manager
        stream = SessionStream.objects.create(
            session=self.session,
            destination_type=DestinationType.RTMP,
            destination_url='rtmp://live.twitch.tv/app/key-1',
            status=StreamStatus.LIVE,
        )
        from apps.streaming.models import StreamDestination

        StreamDestination.objects.create(
            stream=stream,
            url='rtmp://live.twitch.tv/app/key-1',
            label='Twitch',
            status=StreamStatus.LIVE,
        )
        StreamDestination.objects.create(
            stream=stream,
            url='rtmp://a.rtmp.youtube.com/live2/key-2',
            label='YouTube',
            status=StreamStatus.LIVE,
        )

        result = self.service.mark_stream_destination_failed(
            self.session.id,
            'rtmp://live.twitch.tv/app/key-1',
            'connection lost',
        )

        stream.refresh_from_db()
        self.assertEqual(stream.status, StreamStatus.LIVE)
        self.assertEqual(result.status, StreamStatus.LIVE)
        self.assertEqual(
            stream.destinations.filter(status=StreamStatus.FAILED).count(),
            1,
        )
        self.assertEqual(
            stream.destinations.filter(status=StreamStatus.LIVE).count(),
            1,
        )

    @patch('apps.streaming.service.get_session_worker_manager')
    def test_start_stream_requires_ingest_manager(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = False
        mock_get_manager.return_value = mock_manager

        with self.assertRaises(IngestManagerNotRunningError):
            self.service.start_stream(
                self.session.id,
                destination_type=DestinationType.RTMP,
                destination_url='rtmp://live.example.com/app/key',
            )

    @patch('apps.streaming.service.get_session_worker_manager')
    def test_start_stream_rejects_duplicate(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_get_manager.return_value = mock_manager
        SessionStream.objects.create(
            session=self.session,
            destination_type=DestinationType.RTMP,
            destination_url='rtmp://live.example.com/app/key',
            status=StreamStatus.LIVE,
        )

        with self.assertRaises(StreamAlreadyActiveError):
            self.service.start_stream(
                self.session.id,
                destination_type=DestinationType.RTMP,
                destination_url='rtmp://live.example.com/app/other',
            )

    @patch('apps.streaming.service.get_session_worker_manager')
    def test_stop_stream(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_get_manager.return_value = mock_manager
        stream = SessionStream.objects.create(
            session=self.session,
            destination_type=DestinationType.RTMP,
            destination_url='rtmp://live.example.com/app/key',
            status=StreamStatus.LIVE,
        )

        result = self.service.stop_stream(self.session.id)

        mock_manager.send_command.assert_called_once()
        stream.refresh_from_db()
        self.assertEqual(stream.status, StreamStatus.STOPPED)
        self.assertEqual(result.stream_id, stream.id)

    def test_stop_stream_without_active_stream_raises(self):
        with self.assertRaises(StreamNotActiveError):
            self.service.stop_stream(self.session.id)

    @patch('apps.streaming.service.get_session_worker_manager')
    def test_stop_active_stream_if_any(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_manager.is_streaming.return_value = True
        mock_get_manager.return_value = mock_manager
        stream = SessionStream.objects.create(
            session=self.session,
            destination_type=DestinationType.HLS,
            destination_url='/tmp/playlist.m3u8',
            status=StreamStatus.LIVE,
        )

        result = self.service.stop_active_stream_if_any(self.session.id)

        mock_manager.send_command.assert_called_once()
        stream.refresh_from_db()
        self.assertEqual(stream.status, StreamStatus.STOPPED)
        self.assertEqual(result.stream_id, stream.id)

    def test_list_streams(self):
        SessionStream.objects.create(
            session=self.session,
            destination_type=DestinationType.RTMP,
            destination_url='rtmp://live.example.com/app/key',
            status=StreamStatus.STOPPED,
        )

        streams = self.service.list_streams(self.session.id)

        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0].session_id, self.session.id)
