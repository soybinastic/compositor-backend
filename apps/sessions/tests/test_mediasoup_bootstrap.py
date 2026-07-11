import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.sessions.models import StudioSession
from apps.sessions.services.mediasoup_bootstrap import MediasoupMediaPlaneBootstrap
from integrations.mediasoup.exceptions import MediasoupApiError


class MediasoupMediaPlaneBootstrapTests(TestCase):
    @patch('apps.sessions.services.mediasoup_bootstrap.ProducerWatcher')
    @patch('apps.sessions.services.mediasoup_bootstrap.register')
    @patch('apps.sessions.services.mediasoup_bootstrap.SessionIngestManager')
    def test_bootstrap_starts_ingest_manager(
        self,
        mock_manager_cls,
        mock_register,
        mock_watcher_cls,
    ):
        client = MagicMock()
        mock_manager = MagicMock()
        mock_manager_cls.create.return_value = mock_manager
        mock_watcher = MagicMock()
        mock_watcher_cls.instance.return_value = mock_watcher

        session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
        )

        bootstrap = MediasoupMediaPlaneBootstrap(client=client)
        result = bootstrap.bootstrap(session)

        client.create_room.assert_called_once_with(str(session.id))
        client.create_broadcaster.assert_called_once()
        mock_manager_cls.create.assert_called_once()
        mock_register.assert_called_once_with(mock_manager)
        mock_watcher.ensure_running.assert_called_once()
        self.assertTrue(result.mediasoup_compositor_peer_id)

    @patch('apps.sessions.services.mediasoup_bootstrap.unregister')
    def test_teardown_stops_ingest_manager(self, mock_unregister):
        client = MagicMock()
        mock_manager = MagicMock()
        mock_unregister.return_value = mock_manager

        session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id=f'compositor-{uuid.uuid4()}',
        )

        bootstrap = MediasoupMediaPlaneBootstrap(client=client)
        bootstrap.teardown(session)

        mock_manager.stop.assert_called_once()
        client.delete_broadcaster.assert_called_once()
        client.delete_room.assert_called_once_with(str(session.id))

    @patch('apps.sessions.services.mediasoup_bootstrap.unregister')
    def test_teardown_continues_if_room_already_deleted(self, mock_unregister):
        client = MagicMock()
        client.delete_room.side_effect = MediasoupApiError(404, 'not found')
        mock_unregister.return_value = MagicMock()

        session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id='compositor-test',
        )

        bootstrap = MediasoupMediaPlaneBootstrap(client=client)
        bootstrap.teardown(session)

        client.delete_room.assert_called_once()
