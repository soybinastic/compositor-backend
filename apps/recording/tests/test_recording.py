import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.compositor.registry import register, unregister
from apps.compositor.session_ingest_manager import SessionIngestManager
from apps.recording.exceptions import (
    IngestManagerNotRunningError,
    RecordingAlreadyActiveError,
    RecordingNotActiveError,
)
from apps.recording.models import RecordingStatus, SessionRecording
from apps.recording.service import RecordingService
from apps.sessions.models import StudioSession


class RecordingServiceTests(TestCase):
    def setUp(self):
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id='compositor-test',
        )
        self.service = RecordingService()

    def _register_mock_manager(self):
        mock_pipeline = MagicMock()
        mock_pipeline.is_recording.return_value = False
        manager = SessionIngestManager(
            session_id=str(self.session.id),
            room_id=str(self.session.id),
            compositor_peer_id='compositor-test',
            layout='CONTAIN',
            consumer_service=MagicMock(),
            compositor_pipeline=mock_pipeline,
        )
        register(manager)
        self.addCleanup(unregister, str(self.session.id))
        return manager, mock_pipeline

    @override_settings(RECORDINGS_DIR='/tmp/test-recordings')
    @patch('apps.recording.service.get_ingest_manager')
    def test_start_recording_creates_db_record(self, mock_get_manager):
        mock_pipeline = MagicMock()
        mock_manager = MagicMock()
        mock_manager.start_recording = MagicMock()
        mock_get_manager.return_value = mock_manager

        result = self.service.start_recording(self.session.id)

        self.assertEqual(result.status, RecordingStatus.RECORDING)
        self.assertTrue(result.file_path.endswith('.mp4'))
        mock_manager.start_recording.assert_called_once()
        self.assertEqual(SessionRecording.objects.count(), 1)

    @patch('apps.recording.service.get_ingest_manager')
    def test_start_recording_requires_ingest_manager(self, mock_get_manager):
        mock_get_manager.return_value = None

        with self.assertRaises(IngestManagerNotRunningError):
            self.service.start_recording(self.session.id)

    @patch('apps.recording.service.get_ingest_manager')
    def test_start_recording_rejects_duplicate(self, mock_get_manager):
        mock_get_manager.return_value = MagicMock()
        SessionRecording.objects.create(
            session=self.session,
            status=RecordingStatus.RECORDING,
            file_path='/tmp/existing.mp4',
        )

        with self.assertRaises(RecordingAlreadyActiveError):
            self.service.start_recording(self.session.id)

    @patch('apps.recording.service.get_ingest_manager')
    def test_stop_recording_finalizes_db_record(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        recording = SessionRecording.objects.create(
            session=self.session,
            status=RecordingStatus.RECORDING,
            file_path='/tmp/final.mp4',
        )

        result = self.service.stop_recording(self.session.id)

        mock_manager.stop_recording.assert_called_once()
        recording.refresh_from_db()
        self.assertEqual(recording.status, RecordingStatus.STOPPED)
        self.assertEqual(result.recording_id, recording.id)

    def test_stop_recording_without_active_recording_raises(self):
        with self.assertRaises(RecordingNotActiveError):
            self.service.stop_recording(self.session.id)

    @patch('apps.recording.service.get_ingest_manager')
    def test_stop_active_recording_if_any(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_recording.return_value = True
        mock_get_manager.return_value = mock_manager
        recording = SessionRecording.objects.create(
            session=self.session,
            status=RecordingStatus.RECORDING,
            file_path='/tmp/auto-stop.mp4',
        )

        result = self.service.stop_active_recording_if_any(self.session.id)

        mock_manager.stop_recording.assert_called_once()
        recording.refresh_from_db()
        self.assertEqual(recording.status, RecordingStatus.STOPPED)
        self.assertEqual(result.recording_id, recording.id)

    def test_list_recordings(self):
        SessionRecording.objects.create(
            session=self.session,
            status=RecordingStatus.STOPPED,
            file_path='/tmp/one.mp4',
        )

        recordings = self.service.list_recordings(self.session.id)

        self.assertEqual(len(recordings), 1)
        self.assertEqual(recordings[0].session_id, self.session.id)
