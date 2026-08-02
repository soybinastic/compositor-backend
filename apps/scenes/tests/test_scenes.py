"""Tests for scene CRUD and activation."""

import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.scenes.models import SceneType, StudioScene
from apps.sessions.models import LayoutType, SessionStatus, StudioSession
from apps.sessions.services.invite_service import InviteService


@override_settings(
    MEDIASOUP_API_URL='http://mediasoup.test',
    MEDIASOUP_WS_URL='ws://mediasoup.test',
    STUDIO_FRONTEND_URL='http://frontend.test',
)
class SceneApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token=InviteService().generate_token(),
            layout=LayoutType.GRID,
            status=SessionStatus.ACTIVE,
            mediasoup_compositor_peer_id='compositor-1',
            graphics_config={'logo': None},
        )
        self.base = f'/api/v1/sessions/{self.session.id}/scenes'

    def test_list_auto_creates_default_scene(self):
        response = self.client.get(f'{self.base}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'Scene 1')
        self.assertEqual(response.data[0]['layout'], 'GRID')
        self.assertTrue(response.data[0]['is_active'])

        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.active_scene_id)

    def test_create_camera_scene(self):
        self.client.get(f'{self.base}/')
        response = self.client.post(
            f'{self.base}/',
            {'type': SceneType.CAMERA},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['name'], 'Scene 2')
        self.assertEqual(response.data['type'], SceneType.CAMERA)

    @patch('apps.scenes.service.get_session_worker_manager')
    def test_activate_camera_scene(self, mock_get_manager):
        manager = MagicMock()
        manager.is_running.return_value = False
        mock_get_manager.return_value = manager

        list_response = self.client.get(f'{self.base}/')
        scene_id = list_response.data[0]['scene_id']

        scene = StudioScene.objects.get(id=scene_id)
        scene.layout = LayoutType.SPOTLIGHT
        scene.save(update_fields=['layout'])

        response = self.client.post(f'{self.base}/{scene_id}/activate/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['layout'], LayoutType.SPOTLIGHT)

        self.session.refresh_from_db()
        self.assertEqual(self.session.layout, LayoutType.SPOTLIGHT)
        self.assertEqual(str(self.session.active_scene_id), scene_id)

    @patch('apps.scenes.service.send_tile_order_command')
    @patch('apps.scenes.service.get_session_worker_manager')
    def test_activate_camera_scene_sends_tile_order_when_worker_running(
        self,
        mock_get_manager,
        mock_send_tile_order,
    ):
        manager = MagicMock()
        manager.is_running.return_value = True
        mock_get_manager.return_value = manager

        list_response = self.client.get(f'{self.base}/')
        scene_id = list_response.data[0]['scene_id']

        response = self.client.post(f'{self.base}/{scene_id}/activate/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_send_tile_order.assert_called_once()

    @patch('apps.scenes.service.get_session_worker_manager')
    def test_activate_camera_scene_sends_background_music_when_worker_running(
        self,
        mock_get_manager,
    ):
        manager = MagicMock()
        manager.is_running.return_value = True
        mock_get_manager.return_value = manager

        list_response = self.client.get(f'{self.base}/')
        scene_id = list_response.data[0]['scene_id']

        scene = StudioScene.objects.get(id=scene_id)
        scene.background_music_config = {
            'version': 1,
            'enabled': False,
            'track': {
                'asset_id': 'track-1',
                'url': 'https://studio-assets.b-cdn.net/bgm/twilight_drift.mp3',
                'title': 'Twilight Drift',
            },
            'volume': 0.5,
            'loop': True,
            'muted': False,
        }
        scene.save(update_fields=['background_music_config'])

        with patch('apps.scenes.service.send_tile_order_command'):
            response = self.client.post(f'{self.base}/{scene_id}/activate/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        from apps.compositor.commands import UpdateBackgroundMusicCommand

        bgm_calls = [
            call.args[0]
            for call in manager.send_command.call_args_list
            if isinstance(call.args[0], UpdateBackgroundMusicCommand)
        ]
        self.assertEqual(len(bgm_calls), 1)
        command = bgm_calls[0]
        self.assertEqual(command.scene_id, scene_id)
        self.assertEqual(
            command.config['track']['url'],
            'https://studio-assets.b-cdn.net/bgm/twilight_drift.mp3',
        )

    @patch('apps.scenes.service.get_session_worker_manager')
    def test_patch_active_scene_background_music_syncs_to_worker(
        self,
        mock_get_manager,
    ):
        manager = MagicMock()
        manager.is_running.return_value = True
        mock_get_manager.return_value = manager

        list_response = self.client.get(f'{self.base}/')
        scene_id = list_response.data[0]['scene_id']

        with patch('apps.scenes.service.send_tile_order_command'):
            response = self.client.patch(
                f'{self.base}/{scene_id}/',
                {
                    'background_music': {
                        'track': {
                            'asset_id': 'track-2',
                            'url': 'https://studio-assets.b-cdn.net/bgm/morning_light.mp3',
                            'title': 'Morning Light',
                        },
                    },
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        from apps.compositor.commands import UpdateBackgroundMusicCommand

        bgm_calls = [
            call.args[0]
            for call in manager.send_command.call_args_list
            if isinstance(call.args[0], UpdateBackgroundMusicCommand)
        ]
        self.assertEqual(len(bgm_calls), 1)
        self.assertEqual(
            bgm_calls[0].config['track']['url'],
            'https://studio-assets.b-cdn.net/bgm/morning_light.mp3',
        )

    def test_rename_scene(self):
        list_response = self.client.get(f'{self.base}/')
        scene_id = list_response.data[0]['scene_id']

        response = self.client.patch(
            f'{self.base}/{scene_id}/',
            {'name': 'Intro'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Intro')

    def test_cannot_delete_active_scene(self):
        list_response = self.client.get(f'{self.base}/')
        scene_id = list_response.data[0]['scene_id']

        response = self.client.delete(f'{self.base}/{scene_id}/')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_delete_inactive_scene(self):
        self.client.get(f'{self.base}/')
        create_response = self.client.post(
            f'{self.base}/',
            {'type': SceneType.CAMERA},
            format='json',
        )
        scene_id = create_response.data['scene_id']

        response = self.client.delete(f'{self.base}/{scene_id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_get_session_includes_active_scene_id(self):
        self.client.get(f'{self.base}/')
        response = self.client.get(f'/api/v1/sessions/{self.session.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['active_scene_id'])

    def test_create_countdown_scene(self):
        list_response = self.client.get(f'{self.base}/')
        target_id = list_response.data[0]['scene_id']

        response = self.client.post(
            f'{self.base}/',
            {
                'type': SceneType.COUNTDOWN,
                'duration_seconds': 30,
                'target_scene_id': target_id,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['type'], SceneType.COUNTDOWN)
        self.assertEqual(response.data['countdown']['duration_seconds'], 30)

    def test_activate_countdown_scene_starts_countdown(self):
        list_response = self.client.get(f'{self.base}/')
        scene1_id = list_response.data[0]['scene_id']

        scene2_response = self.client.post(
            f'{self.base}/',
            {'type': SceneType.CAMERA},
            format='json',
        )
        scene2_id = scene2_response.data['scene_id']

        create_response = self.client.post(
            f'{self.base}/',
            {
                'type': SceneType.COUNTDOWN,
                'duration_seconds': 10,
                'target_scene_id': scene2_id,
            },
            format='json',
        )
        countdown_id = create_response.data['scene_id']

        with patch('apps.scenes.service.get_session_worker_manager') as mock_get_manager:
            manager = MagicMock()
            manager.is_running.return_value = False
            mock_get_manager.return_value = manager

            response = self.client.post(f'{self.base}/{countdown_id}/activate/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['activation_type'], 'countdown')
        self.assertTrue(response.data['countdown_state']['active'])
        self.assertEqual(response.data['countdown_state']['duration_seconds'], 10)
        self.assertEqual(response.data['countdown_state']['target_scene_id'], scene2_id)

        self.session.refresh_from_db()
        self.assertTrue(self.session.countdown_state['active'])
        # Active camera scene stays unchanged while countdown runs
        self.assertEqual(str(self.session.active_scene_id), scene1_id)

    def test_get_session_includes_countdown_state(self):
        list_response = self.client.get(f'{self.base}/')
        target_id = list_response.data[0]['scene_id']

        create_response = self.client.post(
            f'{self.base}/',
            {
                'type': SceneType.COUNTDOWN,
                'duration_seconds': 5,
                'target_scene_id': target_id,
            },
            format='json',
        )
        countdown_id = create_response.data['scene_id']

        with patch('apps.scenes.service.get_session_worker_manager') as mock_get_manager:
            manager = MagicMock()
            manager.is_running.return_value = False
            mock_get_manager.return_value = manager
            self.client.post(f'{self.base}/{countdown_id}/activate/')

        response = self.client.get(f'/api/v1/sessions/{self.session.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['countdown_state']['active'])

    def test_missing_session_returns_404(self):
        missing = uuid.uuid4()
        response = self.client.get(f'/api/v1/sessions/{missing}/scenes/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
