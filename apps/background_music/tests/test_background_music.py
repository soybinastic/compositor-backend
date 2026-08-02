from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.background_music.exceptions import IngestManagerNotRunningError, SceneNotActiveError
from apps.background_music.service import BackgroundMusicService
from apps.compositor.commands import PlayBackgroundMusicCommand
from apps.scenes.models import SceneType, StudioScene
from apps.sessions.models import SessionStatus, StudioSession


class BackgroundMusicServiceTests(TestCase):
    def setUp(self):
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id='compositor-test',
            status=SessionStatus.ACTIVE,
        )
        self.scene = StudioScene.objects.create(
            session=self.session,
            name='Scene 1',
            scene_type=SceneType.CAMERA,
            sort_order=0,
        )
        self.session.active_scene = self.scene
        self.session.save(update_fields=['active_scene_id'])
        self.service = BackgroundMusicService()

    def test_get_runtime_state_when_worker_not_running_returns_idle(self):
        state = self.service.get_runtime_state(self.session.id)
        self.assertEqual(state['playback_state'], 'idle')
        self.assertEqual(state['scene_id'], str(self.scene.id))

    @patch('apps.background_music.service.get_session_worker_manager')
    def test_get_runtime_state_from_worker(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_manager.send_command.return_value = MagicMock(
            data={
                'scene_id': str(self.scene.id),
                'playback_state': 'playing',
                'position_ms': 1000,
                'duration_ms': 180000,
                'error': None,
                'updated_at': '2026-07-31T12:00:00+00:00',
            }
        )
        mock_get_manager.return_value = mock_manager

        state = self.service.get_runtime_state(self.session.id)

        self.assertEqual(state['playback_state'], 'playing')
        mock_manager.send_command.assert_called_once()

    @patch('apps.background_music.service.get_session_worker_manager')
    def test_play_sends_command(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_manager.send_command.return_value = MagicMock(
            data={
                'scene_id': str(self.scene.id),
                'playback_state': 'playing',
                'position_ms': 0,
                'duration_ms': 180000,
                'error': None,
                'updated_at': '2026-07-31T12:00:00+00:00',
            }
        )
        mock_get_manager.return_value = mock_manager

        ack = self.service.play(self.session.id, self.scene.id)

        self.assertTrue(ack.accepted)
        self.assertEqual(ack.state['playback_state'], 'playing')
        command = mock_manager.send_command.call_args.args[0]
        self.assertIsInstance(command, PlayBackgroundMusicCommand)

    @patch('apps.background_music.service.get_session_worker_manager')
    def test_play_without_track_returns_rejection(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_manager.send_command.side_effect = ValueError('no_track_loaded')
        mock_get_manager.return_value = mock_manager

        ack = self.service.play(self.session.id, self.scene.id)

        self.assertFalse(ack.accepted)
        self.assertEqual(ack.rejection_reason, 'no_track_loaded')

    def test_play_inactive_scene_raises(self):
        other_scene = StudioScene.objects.create(
            session=self.session,
            name='Scene 2',
            scene_type=SceneType.CAMERA,
            sort_order=1,
        )
        with self.assertRaises(SceneNotActiveError):
            self.service.play(self.session.id, other_scene.id)

    @patch('apps.background_music.service.get_session_worker_manager')
    def test_play_requires_ingest_manager(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = False
        mock_get_manager.return_value = mock_manager

        with self.assertRaises(IngestManagerNotRunningError):
            self.service.play(self.session.id, self.scene.id)


class BackgroundMusicApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id='compositor-test',
            status=SessionStatus.ACTIVE,
        )
        self.scene = StudioScene.objects.create(
            session=self.session,
            name='Scene 1',
            scene_type=SceneType.CAMERA,
            sort_order=0,
        )
        self.session.active_scene = self.scene
        self.session.save(update_fields=['active_scene_id'])
        self.state_url = f'/api/v1/sessions/{self.session.id}/background-music/'
        self.play_url = (
            f'/api/v1/sessions/{self.session.id}/scenes/{self.scene.id}/background-music/play/'
        )

    def test_get_runtime_state_returns_idle_without_worker(self):
        response = self.client.get(self.state_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['playback_state'], 'idle')

    @patch('apps.background_music.service.get_session_worker_manager')
    def test_play_returns_503_when_worker_not_running(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = False
        mock_get_manager.return_value = mock_manager

        response = self.client.post(self.play_url)
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @patch('apps.background_music.service.get_session_worker_manager')
    def test_play_inactive_scene_returns_rejection(self, mock_get_manager):
        mock_get_manager.return_value = MagicMock()
        other_scene = StudioScene.objects.create(
            session=self.session,
            name='Scene 2',
            scene_type=SceneType.CAMERA,
            sort_order=1,
        )
        url = (
            f'/api/v1/sessions/{self.session.id}/scenes/{other_scene.id}/background-music/play/'
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['accepted'])
        self.assertEqual(response.data['rejection_reason'], 'scene_not_active')

    @patch('apps.background_music.service.get_session_worker_manager')
    def test_play_success(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.is_running.return_value = True
        mock_manager.send_command.return_value = MagicMock(
            data={
                'scene_id': str(self.scene.id),
                'playback_state': 'playing',
                'position_ms': 0,
                'duration_ms': 180000,
                'error': None,
                'updated_at': '2026-07-31T12:00:00+00:00',
            }
        )
        mock_get_manager.return_value = mock_manager

        response = self.client.post(self.play_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['accepted'])
        self.assertEqual(response.data['state']['playback_state'], 'playing')

    def test_unknown_transport_action_returns_404(self):
        url = (
            f'/api/v1/sessions/{self.session.id}/scenes/{self.scene.id}/background-music/seek/'
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
