import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.sessions.models import StudioSession
from apps.sessions.services.mediasoup_bootstrap import MediasoupMediaPlaneBootstrap
from integrations.mediasoup.exceptions import MediasoupApiError


class MediasoupMediaPlaneBootstrapTests(TestCase):
    @patch('apps.sessions.services.mediasoup_bootstrap.get_session_worker_manager')
    def test_bootstrap_starts_session_worker(self, mock_get_worker_manager):
        client = MagicMock()
        mock_worker_manager = MagicMock()
        mock_get_worker_manager.return_value = mock_worker_manager

        session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
        )

        bootstrap = MediasoupMediaPlaneBootstrap(client=client)
        result = bootstrap.bootstrap(session)

        client.create_room.assert_called_once_with(str(session.id))
        client.create_broadcaster.assert_called_once()
        mock_worker_manager.create_session.assert_called_once()
        self.assertTrue(result.mediasoup_compositor_peer_id)

    @patch('apps.sessions.services.mediasoup_bootstrap.get_session_worker_manager')
    def test_teardown_stops_session_worker(self, mock_get_worker_manager):
        client = MagicMock()
        mock_worker_manager = MagicMock()
        mock_get_worker_manager.return_value = mock_worker_manager

        session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id=f'compositor-{uuid.uuid4()}',
        )

        bootstrap = MediasoupMediaPlaneBootstrap(client=client)
        bootstrap.teardown(session)

        mock_worker_manager.destroy_session.assert_called_once_with(str(session.id))
        client.delete_broadcaster.assert_called_once()
        client.delete_room.assert_called_once_with(str(session.id))

    @patch('apps.sessions.services.mediasoup_bootstrap.get_session_worker_manager')
    def test_teardown_continues_if_room_already_deleted(self, mock_get_worker_manager):
        client = MagicMock()
        client.delete_room.side_effect = MediasoupApiError(404, 'not found')
        mock_worker_manager = MagicMock()
        mock_get_worker_manager.return_value = mock_worker_manager

        session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id='compositor-test',
        )

        bootstrap = MediasoupMediaPlaneBootstrap(client=client)
        bootstrap.teardown(session)

        client.delete_room.assert_called_once()
