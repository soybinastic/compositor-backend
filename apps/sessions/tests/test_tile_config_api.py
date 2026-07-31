"""API tests for session/scene tile order configuration."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.scenes.constants import DEFAULT_SOURCES_CONFIG
from apps.sessions.constants import DEFAULT_TILE_ORDER_CONFIG
from apps.sessions.models import LayoutType, SessionStatus, StudioSession
from apps.sessions.services.invite_service import InviteService


@override_settings(
    MEDIASOUP_API_URL='http://mediasoup.test',
    MEDIASOUP_WS_URL='ws://mediasoup.test',
    STUDIO_FRONTEND_URL='http://frontend.test',
)
class SessionTileConfigApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token=InviteService().generate_token(),
            layout=LayoutType.GRID,
            status=SessionStatus.ACTIVE,
            mediasoup_compositor_peer_id='compositor-1',
        )
        self.session_url = f'/api/v1/sessions/{self.session.id}/'

    def test_get_session_includes_tile_config_defaults(self):
        response = self.client.get(self.session_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['host_peer_id'])
        self.assertEqual(response.data['tile_order_config']['assignments'], {})
        self.assertEqual(response.data['hidden_source_ids'], [])

    def test_patch_session_host_peer_id(self):
        response = self.client.patch(
            self.session_url,
            {'host_peer_id': 'host-peer-abc'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['host_peer_id'], 'host-peer-abc')

        self.session.refresh_from_db()
        self.assertEqual(self.session.host_peer_id, 'host-peer-abc')

    def test_patch_session_tile_order_config(self):
        response = self.client.patch(
            self.session_url,
            {'tile_order_config': {'assignments': {'0': 'host-a', '1': 'guest-b'}}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['tile_order_config']['assignments'],
            {'0': 'host-a', '1': 'guest-b'},
        )

    def test_patch_session_hidden_source_ids(self):
        response = self.client.patch(
            self.session_url,
            {'hidden_source_ids': ['guest-hidden', 'guest-hidden', 'rtmp-x']},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['hidden_source_ids'], ['guest-hidden', 'rtmp-x'])

    def test_patch_session_tile_config_merges_assignments(self):
        self.session.tile_order_config = {
            **DEFAULT_TILE_ORDER_CONFIG,
            'assignments': {'0': 'host-a'},
        }
        self.session.save(update_fields=['tile_order_config'])

        response = self.client.patch(
            self.session_url,
            {'tile_order_config': {'assignments': {'1': 'guest-b'}}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['tile_order_config']['assignments'],
            {'0': 'host-a', '1': 'guest-b'},
        )

    def test_patch_session_empty_body_rejected(self):
        response = self.client.patch(self.session_url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_ended_session_rejected(self):
        self.session.status = SessionStatus.ENDED
        self.session.save(update_fields=['status'])

        response = self.client.patch(
            self.session_url,
            {'host_peer_id': 'host-peer-abc'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    @patch('apps.compositor.tile_order_sync.send_tile_order_command')
    def test_patch_session_sends_tile_order_to_worker(self, mock_send_tile_order):
        response = self.client.patch(
            self.session_url,
            {'host_peer_id': 'host-peer-abc'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_send_tile_order.assert_called_once()


@override_settings(
    MEDIASOUP_API_URL='http://mediasoup.test',
    MEDIASOUP_WS_URL='ws://mediasoup.test',
    STUDIO_FRONTEND_URL='http://frontend.test',
)
class SceneSourcesConfigApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token=InviteService().generate_token(),
            layout=LayoutType.GRID,
            status=SessionStatus.ACTIVE,
            mediasoup_compositor_peer_id='compositor-1',
        )
        self.scenes_url = f'/api/v1/sessions/{self.session.id}/scenes/'

    def test_list_scene_includes_assignments_in_sources(self):
        response = self.client.get(f'{self.scenes_url}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('assignments', response.data[0]['sources'])
        self.assertEqual(response.data[0]['sources']['assignments'], {})

    def test_patch_scene_sources_assignments(self):
        list_response = self.client.get(f'{self.scenes_url}')
        scene_id = list_response.data[0]['scene_id']

        response = self.client.patch(
            f'{self.scenes_url}{scene_id}/',
            {'sources': {'assignments': {'0': 'guest-x', '1': 'host-y'}}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['sources']['assignments'],
            {'0': 'guest-x', '1': 'host-y'},
        )

    def test_patch_scene_sources_merges_with_existing(self):
        list_response = self.client.get(f'{self.scenes_url}')
        scene_id = list_response.data[0]['scene_id']

        self.client.patch(
            f'{self.scenes_url}{scene_id}/',
            {'sources': {'assignments': {'0': 'guest-x'}}},
            format='json',
        )
        response = self.client.patch(
            f'{self.scenes_url}{scene_id}/',
            {'sources': {'assignments': {'1': 'host-y'}}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['sources']['assignments'],
            {'0': 'guest-x', '1': 'host-y'},
        )
        self.assertEqual(response.data['sources']['sources'], [])

    def test_patch_countdown_scene_sources_rejected(self):
        list_response = self.client.get(f'{self.scenes_url}')
        target_id = list_response.data[0]['scene_id']

        create_response = self.client.post(
            f'{self.scenes_url}',
            {
                'type': 'COUNTDOWN',
                'duration_seconds': 10,
                'target_scene_id': target_id,
            },
            format='json',
        )
        countdown_id = create_response.data['scene_id']

        response = self.client.patch(
            f'{self.scenes_url}{countdown_id}/',
            {'sources': {'assignments': {'0': 'guest-x'}}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reset_scene_assignments(self):
        list_response = self.client.get(f'{self.scenes_url}')
        scene_id = list_response.data[0]['scene_id']

        self.client.patch(
            f'{self.scenes_url}{scene_id}/',
            {'sources': {'assignments': {'0': 'guest-x'}}},
            format='json',
        )
        response = self.client.patch(
            f'{self.scenes_url}{scene_id}/',
            {'sources': {'assignments': {}}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['sources']['assignments'], {})
        self.assertEqual(response.data['sources']['version'], DEFAULT_SOURCES_CONFIG['version'])
