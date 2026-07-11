"""Wires mediasoup PlainTransport consumers to the compositor pipeline."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from django.conf import settings

from apps.compositor.compositor_pipeline import CompositorPipeline
from apps.compositor.ingest_branch import IngestStats
from apps.compositor.ports import ParticipantPorts, PortAllocator
from integrations.mediasoup.client import MediasoupHttpClient
from integrations.mediasoup.rtp import (
    build_audio_rtp_capabilities,
    build_video_rtp_capabilities,
    get_codec_payload_type,
)

logger = logging.getLogger(__name__)


@dataclass
class ParticipantIngest:
    participant_peer_id: str
    audio_producer_id: str
    video_producer_id: str
    ports: ParticipantPorts
    audio_consumer_id: str
    video_consumer_id: str


class ConsumerService:
    """
    Creates mediasoup Consumers and feeds RTP into the compositor pipeline.

    Automates the flow from ffmpeg-receiver.sh:
    PlainTransport -> connect -> join -> consume -> resume.
    """

    def __init__(
        self,
        client: MediasoupHttpClient,
        room_id: str,
        compositor_peer_id: str,
        port_allocator: PortAllocator,
        compositor_pipeline: CompositorPipeline,
        *,
        rtp_host: str | None = None,
        audio_payload_type: int,
        video_payload_type: int,
    ) -> None:
        self._client = client
        self._room_id = room_id
        self._compositor_peer_id = compositor_peer_id
        self._port_allocator = port_allocator
        self._compositor_pipeline = compositor_pipeline
        self._rtp_host = rtp_host or settings.COMPOSITOR_RTP_HOST
        self._audio_payload_type = audio_payload_type
        self._video_payload_type = video_payload_type
        self._joined = False

    @property
    def joined(self) -> bool:
        return self._joined

    @classmethod
    def from_router_capabilities(
        cls,
        client: MediasoupHttpClient,
        room_id: str,
        compositor_peer_id: str,
        port_allocator: PortAllocator,
        compositor_pipeline: CompositorPipeline,
        router_caps: dict,
    ) -> ConsumerService:
        return cls(
            client=client,
            room_id=room_id,
            compositor_peer_id=compositor_peer_id,
            port_allocator=port_allocator,
            compositor_pipeline=compositor_pipeline,
            audio_payload_type=get_codec_payload_type(router_caps, 'audio/opus'),
            video_payload_type=get_codec_payload_type(router_caps, 'video/VP8'),
        )

    def attach_participant(
        self,
        participant_peer_id: str,
        audio_producer_id: str,
        video_producer_id: str,
    ) -> ParticipantIngest:
        ports = self._port_allocator.allocate_participant_ports(participant_peer_id)

        # GStreamer must listen before mediasoup sends RTP.
        self._compositor_pipeline.add_participant(
            participant_peer_id,
            audio_port=ports.audio.rtp_port,
            video_port=ports.video.rtp_port,
            audio_payload_type=self._audio_payload_type,
            video_payload_type=self._video_payload_type,
        )

        audio_transport = self._client.create_plain_transport(
            self._room_id,
            self._compositor_peer_id,
        )
        video_transport = self._client.create_plain_transport(
            self._room_id,
            self._compositor_peer_id,
        )

        self._client.connect_plain_transport(
            self._room_id,
            self._compositor_peer_id,
            audio_transport['transportId'],
            ip=self._rtp_host,
            port=ports.audio.rtp_port,
            rtcp_port=ports.audio.rtcp_port,
        )
        self._client.connect_plain_transport(
            self._room_id,
            self._compositor_peer_id,
            video_transport['transportId'],
            ip=self._rtp_host,
            port=ports.video.rtp_port,
            rtcp_port=ports.video.rtcp_port,
        )

        self._ensure_joined()

        audio_consumer = self._client.create_consumer(
            self._room_id,
            self._compositor_peer_id,
            transport_id=audio_transport['transportId'],
            producer_id=audio_producer_id,
            rtp_capabilities=build_audio_rtp_capabilities(self._audio_payload_type),
            paused=False,
        )
        video_consumer = self._client.create_consumer(
            self._room_id,
            self._compositor_peer_id,
            transport_id=video_transport['transportId'],
            producer_id=video_producer_id,
            rtp_capabilities=build_video_rtp_capabilities(self._video_payload_type),
            paused=True,
        )

        time.sleep(0.5)

        self._client.resume_consumer(
            self._room_id,
            self._compositor_peer_id,
            video_consumer['consumerId'],
        )

        logger.info(
            'Attached ingest for participant %s in room %s',
            participant_peer_id,
            self._room_id,
        )

        return ParticipantIngest(
            participant_peer_id=participant_peer_id,
            audio_producer_id=audio_producer_id,
            video_producer_id=video_producer_id,
            ports=ports,
            audio_consumer_id=audio_consumer['consumerId'],
            video_consumer_id=video_consumer['consumerId'],
        )

    def detach_participant(self, participant: ParticipantIngest) -> None:
        self._compositor_pipeline.remove_participant(participant.participant_peer_id)
        self._port_allocator.release_participant_ports(participant.ports)
        logger.info(
            'Detached ingest for participant %s in room %s',
            participant.participant_peer_id,
            self._room_id,
        )

    def get_participant_stats(self, participant_peer_id: str) -> IngestStats | None:
        return self._compositor_pipeline.get_participant_stats(participant_peer_id)

    def _ensure_joined(self) -> None:
        if self._joined:
            return

        self._client.join_broadcaster(self._room_id, self._compositor_peer_id)
        self._joined = True
