from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.compositor.consumer_service import ConsumerService
from apps.compositor.ports import PortAllocator
from apps.compositor.session_ingest_manager import SessionIngestManager
from apps.sessions.models import StudioSession
from integrations.mediasoup.rtp import (
    build_audio_rtp_capabilities,
    get_codec_payload_type,
)


class PortAllocatorTests(TestCase):
    def test_allocates_non_overlapping_ports(self):
        allocator = PortAllocator(min_port=50000, max_port=50020)
        ports_a = allocator.allocate_participant_ports('peer-a')
        ports_b = allocator.allocate_participant_ports('peer-b')

        used = {
            ports_a.audio.rtp_port,
            ports_a.audio.rtcp_port,
            ports_a.video.rtp_port,
            ports_a.video.rtcp_port,
            ports_b.audio.rtp_port,
            ports_b.audio.rtcp_port,
            ports_b.video.rtp_port,
            ports_b.video.rtcp_port,
        }
        self.assertEqual(len(used), 8)

    def test_release_and_reuse_ports(self):
        allocator = PortAllocator(min_port=50000, max_port=50010)
        ports = allocator.allocate_participant_ports('peer-a')
        rtp_port = ports.audio.rtp_port
        allocator.release_participant_ports(ports)
        ports_b = allocator.allocate_participant_ports('peer-b')
        self.assertEqual(ports_b.audio.rtp_port, rtp_port)


class RtpCapabilitiesTests(TestCase):
    def test_extract_payload_types(self):
        router_caps = {
            'codecs': [
                {
                    'kind': 'audio',
                    'mimeType': 'audio/opus',
                    'preferredPayloadType': 100,
                    'clockRate': 48000,
                    'channels': 2,
                },
                {
                    'kind': 'video',
                    'mimeType': 'video/VP8',
                    'preferredPayloadType': 101,
                    'clockRate': 90000,
                },
            ]
        }

        self.assertEqual(get_codec_payload_type(router_caps, 'audio/opus'), 100)
        self.assertEqual(get_codec_payload_type(router_caps, 'video/VP8'), 101)
        audio_caps = build_audio_rtp_capabilities(100)
        self.assertEqual(audio_caps['codecs'][0]['preferredPayloadType'], 100)


class SessionIngestManagerTests(TestCase):
    def test_sync_producers_attaches_and_detaches(self):
        session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id='compositor-test',
        )

        mock_consumer_service = MagicMock(spec=ConsumerService)
        mock_consumer_service.joined = False
        mock_participant = MagicMock()
        mock_consumer_service.attach_participant.return_value = mock_participant
        mock_compositor_pipeline = MagicMock()

        manager = SessionIngestManager(
            session_id=str(session.id),
            room_id=str(session.id),
            compositor_peer_id='compositor-test',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=mock_compositor_pipeline,
        )

        manager.sync_producers(
            [
                {
                    'peerId': 'guest-1',
                    'producers': [
                        {
                            'producerId': 'audio-1',
                            'kind': 'audio',
                            'source': 'audio',
                        },
                        {
                            'producerId': 'video-1',
                            'kind': 'video',
                            'source': 'video',
                        },
                    ],
                }
            ]
        )

        mock_consumer_service.attach_participant.assert_called_once_with(
            'guest-1',
            'audio-1',
            'video-1',
        )

        manager.sync_producers([])
        mock_consumer_service.detach_participant.assert_called_once_with(mock_participant)

    def test_set_layout_updates_compositor_pipeline(self):
        mock_consumer_service = MagicMock(spec=ConsumerService)
        mock_compositor_pipeline = MagicMock()
        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=mock_compositor_pipeline,
        )

        manager.set_layout('THUMBNAIL')

        self.assertEqual(manager.layout, 'THUMBNAIL')
        mock_compositor_pipeline.set_layout.assert_called_once_with('THUMBNAIL')

    def test_skips_compositor_peer(self):
        mock_consumer_service = MagicMock(spec=ConsumerService)
        mock_compositor_pipeline = MagicMock()
        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=mock_compositor_pipeline,
        )

        manager.sync_producers(
            [
                {
                    'peerId': 'compositor-session-1',
                    'producers': [],
                }
            ]
        )

        mock_consumer_service.attach_participant.assert_not_called()
