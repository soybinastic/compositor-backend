"""API tests for session graphics endpoints."""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.sessions.models import LayoutType, SessionStatus, StudioSession
from apps.sessions.services.invite_service import InviteService


@override_settings(
    MEDIASOUP_API_URL='http://mediasoup.test',
    MEDIASOUP_WS_URL='ws://mediasoup.test',
    STUDIO_FRONTEND_URL='http://frontend.test',
)
class GraphicsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token=InviteService().generate_token(),
            layout=LayoutType.CONTAIN,
            status=SessionStatus.ACTIVE,
            mediasoup_compositor_peer_id='compositor-1',
            graphics_config={},
        )
        self.base = f'/api/v1/sessions/{self.session.id}/graphics'

    def test_get_empty_graphics(self):
        response = self.client.get(f'{self.base}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['background'])
        self.assertIsNone(response.data['logo'])

    @patch('apps.compositor.registry.get')
    def test_update_background_persists(self, mock_get):
        mock_get.return_value = None
        response = self.client.post(
            f'{self.base}/background/',
            {'url': 'https://cdn.example.com/bg.png', 'fit': 'cover'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.graphics_config['background']['url'],
            'https://cdn.example.com/bg.png',
        )
        self.assertEqual(response.data['background']['url'], 'https://cdn.example.com/bg.png')

    @patch('apps.compositor.registry.get')
    def test_bulk_partial_merge(self, mock_get):
        mock_get.return_value = None
        self.session.graphics_config = {
            'background': None,
            'overlay': None,
            'logo': {'url': 'https://cdn.example.com/logo.png', 'is_active': True},
            'qr': None,
            'banner': None,
            'ticker': None,
            'chat': None,
        }
        self.session.save(update_fields=['graphics_config'])

        response = self.client.post(
            f'{self.base}/bulk/',
            {
                'ticker': {'tickerText': 'Live now', 'tickerEnabled': True},
                'qr': {
                    'url': 'https://cdn.example.com/qr.png',
                    'is_shown': True,
                },
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['logo']['url'], 'https://cdn.example.com/logo.png')
        self.assertEqual(response.data['ticker']['tickerText'], 'Live now')
        self.assertTrue(response.data['qr']['is_shown'])

    @patch('apps.compositor.registry.get')
    def test_banner_ticker_endpoint(self, mock_get):
        mock_get.return_value = None
        response = self.client.post(
            f'{self.base}/banner-ticker/',
            {
                'banner': {
                    'title': 'Guest',
                    'description': 'Host',
                    'is_display': True,
                },
                'ticker': {'tickerText': 'Welcome'},
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['banner']['title'], 'Guest')
        self.assertEqual(response.data['ticker']['tickerText'], 'Welcome')

    @patch('apps.compositor.registry.get')
    def test_chat_endpoint(self, mock_get):
        mock_get.return_value = None
        response = self.client.post(
            f'{self.base}/chat/',
            {
                'enabled': True,
                'messages': [{'author': 'A', 'text': 'Hi'}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['chat']['enabled'])

    @patch('apps.compositor.registry.get')
    def test_ended_session_conflict(self, mock_get):
        mock_get.return_value = None
        self.session.status = SessionStatus.ENDED
        self.session.save(update_fields=['status'])
        response = self.client.post(
            f'{self.base}/logo/',
            {'url': 'https://cdn.example.com/l.png', 'is_active': True},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    @patch('apps.compositor.registry.get')
    def test_apply_called_when_ingest_running(self, mock_get):
        manager = MagicMock()
        mock_get.return_value = manager
        response = self.client.post(
            f'{self.base}/overlay/',
            {'url': 'https://cdn.example.com/o.png', 'is_active': True},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        manager.apply_graphics.assert_called_once()
        args, kwargs = manager.apply_graphics.call_args
        self.assertFalse(kwargs.get('layout_only'))
        self.assertTrue(args[0]['overlay']['is_active'])
