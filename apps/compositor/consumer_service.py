"""Wires mediasoup PlainTransport consumers to the compositor pipeline."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from django.conf import settings

from apps.compositor.compositor_pipeline import CompositorPipeline, MediasoupTransportTuple
from apps.compositor.ingest_branch import IngestStats
from apps.compositor.ports import ParticipantPorts, PortAllocator
from integrations.mediasoup.client import MediasoupHttpClient
from integrations.mediasoup.rtp import (
    build_audio_rtp_capabilities,
    build_video_rtp_capabilities,
    get_codec_payload_type,
    get_payload_type_from_rtp_parameters,
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
    PlainTransport -> connect -> join -> consume (paused) -> listen -> resume.
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

        audio_transport = self._client.create_plain_transport(
            self._room_id,
            self._compositor_peer_id,
            rtcp_mux=False,
        )
        video_transport = self._client.create_plain_transport(
            self._room_id,
            self._compositor_peer_id,
            rtcp_mux=False,
        )

        self._client.connect_plain_transport(
            self._room_id,
            self._compositor_peer_id,
            audio_transport['transportId'],
            ip=self._rtp_host,
            port=ports.audio.rtp_port,
            rtcp_port=ports.audio.rtcp_port,
            rtcp_mux=False,
        )
        self._client.connect_plain_transport(
            self._room_id,
            self._compositor_peer_id,
            video_transport['transportId'],
            ip=self._rtp_host,
            port=ports.video.rtp_port,
            rtcp_port=ports.video.rtcp_port,
            rtcp_mux=False,
        )

        self._ensure_joined()

        # Create paused so we can read wire payload types before GStreamer listens,
        # then resume only after udpsrc caps match consumer rtpParameters.
        audio_consumer = self._client.create_consumer(
            self._room_id,
            self._compositor_peer_id,
            transport_id=audio_transport['transportId'],
            producer_id=audio_producer_id,
            rtp_capabilities=build_audio_rtp_capabilities(self._audio_payload_type),
            paused=True,
        )
        video_consumer = self._client.create_consumer(
            self._room_id,
            self._compositor_peer_id,
            transport_id=video_transport['transportId'],
            producer_id=video_producer_id,
            rtp_capabilities=build_video_rtp_capabilities(self._video_payload_type),
            paused=True,
        )

        if 'rtpParameters' not in audio_consumer or 'rtpParameters' not in video_consumer:
            raise RuntimeError(
                'mediasoup consume response missing rtpParameters '
                f'(audio keys={list(audio_consumer.keys())}, '
                f'video keys={list(video_consumer.keys())}). '
                'Restart mediasoup-backend after rebuilding TypeScript '
                '(npm run typescript:build && ./start.sh).'
            )

        audio_wire_pt = get_payload_type_from_rtp_parameters(
            audio_consumer['rtpParameters']
        )
        video_wire_pt = get_payload_type_from_rtp_parameters(
            video_consumer['rtpParameters']
        )

        logger.info(
            'Consumer wire payload types for peer %s: audio=%s (router=%s) video=%s (router=%s)',
            participant_peer_id,
            audio_wire_pt,
            self._audio_payload_type,
            video_wire_pt,
            self._video_payload_type,
        )

        self._compositor_pipeline.add_participant(
            participant_peer_id,
            audio_port=ports.audio.rtp_port,
            video_port=ports.video.rtp_port,
            audio_rtcp_port=ports.audio.rtcp_port,
            video_rtcp_port=ports.video.rtcp_port,
            audio_payload_type=audio_wire_pt,
            video_payload_type=video_wire_pt,
            audio_mediasoup_transport=_plain_transport_tuple(audio_transport),
            video_mediasoup_transport=_plain_transport_tuple(video_transport),
            rtcp_mux=False,
        )

        self._client.resume_consumer(
            self._room_id,
            self._compositor_peer_id,
            audio_consumer['consumerId'],
        )
        self._client.resume_consumer(
            self._room_id,
            self._compositor_peer_id,
            video_consumer['consumerId'],
        )

        participant = ParticipantIngest(
            participant_peer_id=participant_peer_id,
            audio_producer_id=audio_producer_id,
            video_producer_id=video_producer_id,
            ports=ports,
            audio_consumer_id=audio_consumer['consumerId'],
            video_consumer_id=video_consumer['consumerId'],
        )
        self._schedule_video_keyframe_retries(participant)

        logger.info(
            'Attached ingest for participant %s in room %s '
            '(audio=%s:%s/%s->%s:%s video=%s:%s/%s->%s:%s producers audio=%s video=%s)',
            participant_peer_id,
            self._room_id,
            self._rtp_host,
            ports.audio.rtp_port,
            ports.audio.rtcp_port,
            audio_transport['ip'],
            audio_transport.get('rtcpPort', audio_transport['port']),
            self._rtp_host,
            ports.video.rtp_port,
            ports.video.rtcp_port,
            video_transport['ip'],
            video_transport.get('rtcpPort', video_transport['port']),
            audio_producer_id,
            video_producer_id,
        )

        return participant

    def _schedule_video_keyframe_retries(self, participant: ParticipantIngest) -> None:
        """
        Re-hit resume (mediasoup requests a PLI/keyframe) until video decodes.

        rtpvp8depay waits for a keyframe; if the first ones are incomplete we
        otherwise sit at decoded(v=0) while RTP keeps climbing.
        """

        def _retry() -> None:
            for attempt in range(1, 6):
                time.sleep(1.0)
                stats = self.get_participant_stats(participant.participant_peer_id)
                if stats is None:
                    return
                if stats.video_buffers > 0:
                    return
                try:
                    self._client.resume_consumer(
                        self._room_id,
                        self._compositor_peer_id,
                        participant.video_consumer_id,
                    )
                    logger.info(
                        'Requested video keyframe retry=%s peer=%s (decoded still 0)',
                        attempt,
                        participant.participant_peer_id,
                    )
                except Exception:
                    logger.exception(
                        'Video keyframe retry failed peer=%s',
                        participant.participant_peer_id,
                    )
                    return

        threading.Thread(
            target=_retry,
            name=f'keyframe-{participant.participant_peer_id}',
            daemon=True,
        ).start()

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


def _plain_transport_tuple(transport: dict) -> MediasoupTransportTuple:
    ip = transport['ip']
    if ip in ('0.0.0.0', '::', '[::]'):
        ip = settings.COMPOSITOR_RTP_HOST

    rtcp_port = transport.get('rtcpPort')
    return MediasoupTransportTuple(
        ip=ip,
        port=int(transport['port']),
        rtcp_port=int(rtcp_port) if rtcp_port is not None else None,
    )
