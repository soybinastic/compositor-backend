"""Per-session RTP ingest and compositor orchestration."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from apps.compositor.compositor_pipeline import CompositorPipeline
from apps.compositor.consumer_service import ConsumerService, ParticipantIngest
from apps.compositor.ports import PortAllocator
from apps.sessions.models import StudioSession
from integrations.mediasoup.client import MediasoupHttpClient

logger = logging.getLogger(__name__)


@dataclass
class ParticipantIngestStatus:
    participant_peer_id: str
    audio_producer_id: str
    video_producer_id: str
    audio_port: int
    video_port: int
    audio_buffers: int
    video_buffers: int
    rtp_audio_packets: int
    rtp_video_packets: int
    rtcp_audio_packets: int
    rtcp_video_packets: int


@dataclass
class RtmpSourceIngestStatus:
    source_id: str
    url: str
    display_name: str
    video_buffers: int
    audio_buffers: int


@dataclass
class SessionIngestStatus:
    session_id: str
    room_id: str
    compositor_peer_id: str
    layout: str
    joined: bool
    composited_frames: int
    canvas_width: int
    canvas_height: int
    host_peer_id: str | None
    recording_active: bool
    recording_file_path: str | None
    streaming_active: bool
    streaming_destination_type: str | None
    streaming_destination_url: str | None
    participants: list[ParticipantIngestStatus] = field(default_factory=list)
    rtmp_sources: list[RtmpSourceIngestStatus] = field(default_factory=list)


class SessionIngestManager:
    """Manages RTP ingest and compositor layout for one studio session."""

    def __init__(
        self,
        session_id: str,
        room_id: str,
        compositor_peer_id: str,
        layout: str,
        consumer_service: ConsumerService,
        compositor_pipeline: CompositorPipeline,
    ) -> None:
        self.session_id = session_id
        self.room_id = room_id
        self.compositor_peer_id = compositor_peer_id
        self.layout = layout
        self._consumer_service = consumer_service
        self._compositor_pipeline = compositor_pipeline
        self._participants: dict[str, ParticipantIngest] = {}
        self._rtmp_sources: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()
        self._stopped = False

    @classmethod
    def create(
        cls,
        session: StudioSession,
        client: MediasoupHttpClient | None = None,
    ) -> SessionIngestManager:
        client = client or MediasoupHttpClient()
        room_id = str(session.id)
        compositor_peer_id = session.mediasoup_compositor_peer_id
        if not compositor_peer_id:
            raise ValueError(f'Session {session.id} has no compositor peer id')

        compositor_pipeline = CompositorPipeline(
            str(session.id),
            width=settings.CANVAS_WIDTH,
            height=settings.CANVAS_HEIGHT,
            fps=settings.CANVAS_FPS,
            layout=session.layout,
        )
        compositor_pipeline.set_stream_failure_handler(
            lambda reason, session_id=str(session.id): _handle_stream_failure(session_id, reason)
        )
        compositor_pipeline.start()

        router = client.get_router_rtp_capabilities(room_id)
        router_caps = router.get('routerRtpCapabilities', {})

        consumer_service = ConsumerService.from_router_capabilities(
            client=client,
            room_id=room_id,
            compositor_peer_id=compositor_peer_id,
            port_allocator=PortAllocator(),
            compositor_pipeline=compositor_pipeline,
            router_caps=router_caps,
        )

        return cls(
            session_id=str(session.id),
            room_id=room_id,
            compositor_peer_id=compositor_peer_id,
            layout=session.layout,
            consumer_service=consumer_service,
            compositor_pipeline=compositor_pipeline,
        )

    def set_layout(self, layout: str) -> None:
        with self._lock:
            self.layout = layout
            self._compositor_pipeline.set_layout(layout)

    def sync_producers(self, peer_producers_infos: list[dict[str, Any]]) -> None:
        """Attach or detach participants based on mediasoup producer state."""
        if self._stopped:
            return

        desired: dict[str, tuple[str, str]] = {}

        for peer_info in peer_producers_infos:
            participant_peer_id = peer_info['peerId']

            if participant_peer_id == self.compositor_peer_id:
                continue
            if participant_peer_id.startswith('compositor-'):
                continue

            audio_id, video_id = self._extract_av_producers(peer_info)
            if audio_id and video_id:
                desired[participant_peer_id] = (audio_id, video_id)

        with self._lock:
            for participant_peer_id in list(self._participants.keys()):
                if participant_peer_id not in desired:
                    continue

                audio_id, video_id = desired[participant_peer_id]
                current = self._participants[participant_peer_id]
                if (
                    current.audio_producer_id != audio_id
                    or current.video_producer_id != video_id
                ):
                    logger.info(
                        'Re-attaching ingest for %s (producer ids changed)',
                        participant_peer_id,
                    )
                    self._consumer_service.detach_participant(current)
                    del self._participants[participant_peer_id]

            current_ids = set(self._participants.keys())
            desired_ids = set(desired.keys())

            for participant_peer_id in desired_ids - current_ids:
                audio_id, video_id = desired[participant_peer_id]
                try:
                    participant = self._consumer_service.attach_participant(
                        participant_peer_id,
                        audio_id,
                        video_id,
                    )
                    self._participants[participant_peer_id] = participant
                except Exception:
                    logger.exception(
                        'Failed to attach ingest for participant %s',
                        participant_peer_id,
                    )

            for participant_peer_id in current_ids - desired_ids:
                participant = self._participants.pop(participant_peer_id)
                self._consumer_service.detach_participant(participant)

    def add_rtmp_source(
        self,
        *,
        source_id: str,
        url: str,
        display_name: str = '',
    ) -> None:
        self._compositor_pipeline.add_rtmp_source(
            source_id,
            url=url,
            display_name=display_name,
        )
        with self._lock:
            self._rtmp_sources[source_id] = {
                'url': url,
                'display_name': display_name,
            }

    def remove_rtmp_source(self, source_id: str) -> None:
        self._compositor_pipeline.remove_rtmp_source(source_id)
        with self._lock:
            self._rtmp_sources.pop(source_id, None)

    def get_rtmp_source_stats(self, source_id: str):
        return self._compositor_pipeline.get_rtmp_source_stats(source_id)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            for source_id in list(self._rtmp_sources.keys()):
                self._compositor_pipeline.remove_rtmp_source(source_id)
            self._rtmp_sources.clear()
            for participant in list(self._participants.values()):
                self._consumer_service.detach_participant(participant)
            self._participants.clear()
            self._compositor_pipeline.stop()

    def start_recording(self, file_path) -> None:
        self._compositor_pipeline.start_recording(file_path)

    def stop_recording(self):
        return self._compositor_pipeline.stop_recording()

    def is_recording(self) -> bool:
        return self._compositor_pipeline.is_recording()

    def start_stream(
        self,
        *,
        destination_type: str,
        destination_url: str,
        output_dir=None,
    ) -> None:
        self._compositor_pipeline.start_streaming(
            destination_type=destination_type,
            destination_url=destination_url,
            output_dir=output_dir,
        )

    def stop_stream(self) -> None:
        self._compositor_pipeline.stop_streaming()

    def is_streaming(self) -> bool:
        return self._compositor_pipeline.is_streaming()

    def get_status(self) -> SessionIngestStatus:
        pipeline_status = self._compositor_pipeline.get_status()

        with self._lock:
            participants = []
            for participant in self._participants.values():
                stats = self._consumer_service.get_participant_stats(
                    participant.participant_peer_id
                )
                participants.append(
                    ParticipantIngestStatus(
                        participant_peer_id=participant.participant_peer_id,
                        audio_producer_id=participant.audio_producer_id,
                        video_producer_id=participant.video_producer_id,
                        audio_port=participant.ports.audio.rtp_port,
                        video_port=participant.ports.video.rtp_port,
                        audio_buffers=stats.audio_buffers if stats else 0,
                        video_buffers=stats.video_buffers if stats else 0,
                        rtp_audio_packets=stats.rtp_audio_packets if stats else 0,
                        rtp_video_packets=stats.rtp_video_packets if stats else 0,
                        rtcp_audio_packets=stats.rtcp_audio_packets if stats else 0,
                        rtcp_video_packets=stats.rtcp_video_packets if stats else 0,
                    )
                )

            rtmp_sources = []
            for source_id, meta in self._rtmp_sources.items():
                stats = self._compositor_pipeline.get_rtmp_source_stats(source_id)
                rtmp_sources.append(
                    RtmpSourceIngestStatus(
                        source_id=source_id,
                        url=meta['url'],
                        display_name=meta['display_name'],
                        video_buffers=stats.video_buffers if stats else 0,
                        audio_buffers=stats.audio_buffers if stats else 0,
                    )
                )

            return SessionIngestStatus(
                session_id=self.session_id,
                room_id=self.room_id,
                compositor_peer_id=self.compositor_peer_id,
                layout=pipeline_status.layout,
                joined=self._consumer_service.joined or bool(participants),
                composited_frames=pipeline_status.composited_frames,
                canvas_width=pipeline_status.canvas_width,
                canvas_height=pipeline_status.canvas_height,
                host_peer_id=pipeline_status.host_peer_id,
                recording_active=pipeline_status.recording_active,
                recording_file_path=pipeline_status.recording_file_path,
                streaming_active=pipeline_status.streaming_active,
                streaming_destination_type=pipeline_status.streaming_destination_type,
                streaming_destination_url=pipeline_status.streaming_destination_url,
                participants=participants,
                rtmp_sources=rtmp_sources,
            )

    @staticmethod
    def _extract_av_producers(peer_info: dict[str, Any]) -> tuple[str | None, str | None]:
        audio_id: str | None = None
        video_id: str | None = None

        for producer in peer_info.get('producers', []):
            kind = producer.get('kind')
            source = producer.get('source')

            if kind == 'audio' and source == 'audio':
                audio_id = producer['producerId']
            elif kind == 'video' and source in ('video', 'screensharing'):
                video_id = producer['producerId']

        return audio_id, video_id


def _handle_stream_failure(session_id: str, reason: str) -> None:
    from apps.streaming.service import StreamingService

    StreamingService().mark_active_stream_failed(uuid.UUID(session_id), reason)
