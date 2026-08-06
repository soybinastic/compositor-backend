import threading
import time
import uuid
from unittest.mock import MagicMock

from django.test import TestCase, override_settings

from apps.compositor.session_producer_poller import (
    SessionProducerPoller,
    get_poller_registry,
    start_session_producer_poller,
    stop_session_producer_poller,
)


class SessionProducerPollerTests(TestCase):
    def tearDown(self):
        get_poller_registry().shutdown_all()

    @override_settings(PRODUCER_POLL_INTERVAL=0.05)
    def test_poller_invokes_sync_callback(self):
        synced: list[list] = []
        lock = threading.Lock()
        client = MagicMock()
        client.get_producers.return_value = {
            'peerProducersInfos': [
                {
                    'peerId': 'guest-1',
                    'displayName': 'Guest',
                    'producers': [],
                }
            ],
            'joinedPeers': [{'peerId': 'guest-1', 'displayName': 'Guest'}],
        }

        def sync_producers(infos, joined_peers=None):
            with lock:
                synced.append((infos, joined_peers or []))

        poller = SessionProducerPoller(
            'session-1',
            'room-1',
            sync_producers=sync_producers,
            client=client,
            interval_sec=0.05,
        )
        poller.start()
        try:
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                with lock:
                    if synced:
                        break
                time.sleep(0.02)
        finally:
            poller.stop()

        self.assertEqual(len(synced), 1)
        self.assertEqual(synced[0][0][0]['peerId'], 'guest-1')
        self.assertEqual(synced[0][1][0]['peerId'], 'guest-1')
        client.get_producers.assert_called_with('room-1')

    def test_start_and_stop_registry_helpers(self):
        session_id = str(uuid.uuid4())
        calls: list[str] = []

        start_session_producer_poller(
            session_id,
            session_id,
            sync_producers=lambda _infos, _joined=None: calls.append(session_id),
            client=MagicMock(get_producers=MagicMock(return_value={
                'peerProducersInfos': [],
                'joinedPeers': [],
            })),
        )
        self.assertEqual(get_poller_registry().count(), 1)

        stop_session_producer_poller(session_id)
        self.assertEqual(get_poller_registry().count(), 0)
