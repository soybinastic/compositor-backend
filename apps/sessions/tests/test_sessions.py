import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.sessions.models import LayoutType, SessionStatus, StudioSession
from apps.sessions.services.session_service import SessionService
from core.interfaces import IMediaPlaneBootstrap
from integrations.mediasoup.client import MediasoupHttpClient
from integrations.mediasoup.exceptions import MediasoupApiError


class FakeMediaPlaneBootstrap(IMediaPlaneBootstrap):
    """In-memory bootstrap for unit tests (no mediasoup required)."""

    def bootstrap(self, session: StudioSession) -> StudioSession:
        session.mediasoup_compositor_peer_id = f'compositor-{session.id}'
        session.save()
        return session

    def teardown(self, session: StudioSession) -> None:
        pass


class SessionServiceTests(TestCase):
    def setUp(self):
        self.service = SessionService(media_plane_bootstrap=FakeMediaPlaneBootstrap())

    def test_create_session(self):
        result = self.service.create_session(host_display_name='Alice')
        session = result.session

        self.assertEqual(session.status, SessionStatus.ACTIVE)
        self.assertEqual(session.layout, LayoutType.CONTAIN)
        self.assertTrue(session.mediasoup_compositor_peer_id)
        self.assertIn('/join/', result.invite_url)
        self.assertIn('token=', result.invite_url)
        self.assertEqual(result.mediasoup_ws_url, 'ws://localhost:4443')

    def test_update_layout(self):
        result = self.service.create_session(host_display_name='Alice')
        updated = self.service.update_layout(result.session.id, LayoutType.THUMBNAIL)
        self.assertEqual(updated.layout, LayoutType.THUMBNAIL)

    def test_end_session(self):
        result = self.service.create_session(host_display_name='Alice')
        ended = self.service.end_session(result.session.id)
        self.assertEqual(ended.status, SessionStatus.ENDED)
        self.assertIsNotNone(ended.ended_at)

    def test_validate_invite(self):
        result = self.service.create_session(host_display_name='Alice')
        validation = self.service.validate_invite(
            result.session.id,
            result.session.invite_token,
        )
        self.assertEqual(validation.session_id, str(result.session.id))
        self.assertEqual(validation.room_id, str(result.session.id))


class SessionApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.bootstrap_patcher = patch(
            'apps.sessions.services.session_service.MediasoupMediaPlaneBootstrap',
            FakeMediaPlaneBootstrap,
        )
        self.bootstrap_patcher.start()

    def tearDown(self):
        self.bootstrap_patcher.stop()

    def test_create_and_get_session(self):
        create_response = self.client.post(
            '/api/v1/sessions/',
            {'host_display_name': 'Bob', 'layout': 'CONTAIN'},
            format='json',
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        session_id = create_response.data['session_id']
        get_response = self.client.get(f'/api/v1/sessions/{session_id}/')
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        self.assertEqual(get_response.data['host_display_name'], 'Bob')

    def test_validate_invite_rejects_bad_token(self):
        create_response = self.client.post(
            '/api/v1/sessions/',
            {'host_display_name': 'Carol'},
            format='json',
        )
        session_id = create_response.data['session_id']

        response = self.client.post(
            f'/api/v1/sessions/{session_id}/validate-invite/',
            {'invite_token': 'invalid'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_update_layout_after_session_ended(self):
        create_response = self.client.post(
            '/api/v1/sessions/',
            {'host_display_name': 'Dana'},
            format='json',
        )
        session_id = create_response.data['session_id']
        self.client.delete(f'/api/v1/sessions/{session_id}/')

        response = self.client.patch(
            f'/api/v1/sessions/{session_id}/layout/',
            {'layout': 'THUMBNAIL'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_get_missing_session_returns_404(self):
        missing_id = uuid.uuid4()
        response = self.client.get(f'/api/v1/sessions/{missing_id}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class MediasoupHttpClientTests(TestCase):
    @patch('integrations.mediasoup.client.urllib.request.urlopen')
    def test_create_room_sends_origin_header(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.status = 201
        mock_response.read.return_value = b'{"roomId":"abc"}'
        mock_response.headers.get.return_value = 'application/json'
        mock_urlopen.return_value = mock_response

        client = MediasoupHttpClient(
            api_url='http://localhost:4443',
            origin='http://localhost:4443',
        )
        result = client.create_room('abc')

        self.assertEqual(result['roomId'], 'abc')
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.full_url, 'http://localhost:4443/rooms')
        self.assertEqual(request.get_header('Origin'), 'http://localhost:4443')

    @patch('integrations.mediasoup.client.urllib.request.urlopen')
    def test_delete_room_raises_on_error(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            url='http://localhost:4443/rooms/abc',
            code=404,
            msg='Not Found',
            hdrs=None,
            fp=MagicMock(read=MagicMock(return_value=b"Room not found")),
        )

        client = MediasoupHttpClient(
            api_url='http://localhost:4443',
            origin='http://localhost:4443',
        )

        with self.assertRaises(MediasoupApiError) as ctx:
            client.delete_room('abc')

        self.assertEqual(ctx.exception.status, 404)
