"""Per-session RTP ingest and compositor orchestration."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from apps.compositor.compositor_pipeline import CompositorPipeline
from apps.compositor.consumer_service import ConsumerService, ParticipantIngest
from apps.compositor.ports import PortAllocator
from apps.compositor.tile_order_sync import apply_tile_order_to_pipeline
from apps.sessions.models import StudioSession
from integrations.mediasoup.client import MediasoupHttpClient

logger = logging.getLogger(__name__)


def _video_soft_disable_grace_sec() -> float:
    return float(getattr(settings, 'VIDEO_SOFT_DISABLE_GRACE_SEC', 0.0))


@dataclass
class ParticipantIngestStatus:
    participant_peer_id: str
    audio_producer_id: str
    video_producer_id: str | None
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
    video_backend: str | None
    requested_video_backend: str
    streaming_destination_urls: list[str] = field(default_factory=list)
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
        # peer_id → monotonic time when video producer first went missing
        self._video_missing_since: dict[str, float] = {}
        self._display_names: dict[str, str] = {}

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
            video_backend=settings.COMPOSITOR_VIDEO_BACKEND,
            cuda_device_id=settings.COMPOSITOR_CUDA_DEVICE_ID,
        )
        compositor_pipeline.start()

        apply_tile_order_to_pipeline(compositor_pipeline, session)

        # Restore persisted graphics onto the live canvas when present.
        graphics_config = getattr(session, 'graphics_config', None) or {}
        if any(graphics_config.values() if isinstance(graphics_config, dict) else []):
            try:
                compositor_pipeline.apply_graphics(graphics_config, layout_only=False)
            except Exception:
                logger.exception(
                    'Failed to restore graphics for session %s',
                    session.id,
                )

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

    def set_layout(self, layout: str, *, graphics_state: dict | None = None) -> None:
        with self._lock:
            self.layout = layout
            self._compositor_pipeline.set_layout(layout, graphics_state=graphics_state)

    def set_tile_order(
        self,
        *,
        host_peer_id: str | None = None,
        slot_assignments: dict[str, str] | None = None,
        hidden_source_ids: list[str] | None = None,
    ) -> None:
        self._compositor_pipeline.set_tile_order(
            host_peer_id=host_peer_id,
            slot_assignments=slot_assignments,
            hidden_source_ids=hidden_source_ids,
        )

    def apply_graphics(self, state: dict, *, layout_only: bool = False) -> None:
        self._compositor_pipeline.apply_graphics(state, layout_only=layout_only)

    def start_countdown(self, *, started_at_epoch: float, duration_seconds: int) -> None:
        self._compositor_pipeline.start_countdown(
            started_at_epoch=started_at_epoch,
            duration_seconds=duration_seconds,
        )

    def stop_countdown(self) -> None:
        self._compositor_pipeline.stop_countdown()

    def sync_producers(
        self,
        peer_producers_infos: list[dict[str, Any]],
        joined_peers: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Attach / soft-disable / detach based on producers + room presence.

        Sticky peers (still joined) keep their layout seat when webcam closes.
        Missing video becomes an initials placeholder immediately (grace default 0).
        Re-enable uses full re-attach when a video producer returns.
        """
        if self._stopped:
            return

        joined_peers = joined_peers or []
        producers_by_peer: dict[str, dict[str, Any]] = {}
        for peer_info in peer_producers_infos:
            peer_id = peer_info.get('peerId')
            if not peer_id or not isinstance(peer_id, str):
                continue
            if self._is_compositor_peer(peer_id):
                continue
            producers_by_peer[peer_id] = peer_info
            display_name = peer_info.get('displayName')
            if isinstance(display_name, str) and display_name.strip():
                self._display_names[peer_id] = display_name.strip()

        stage_roster: set[str] = set()
        for peer in joined_peers:
            peer_id = peer.get('peerId') if isinstance(peer, dict) else None
            if not peer_id or not isinstance(peer_id, str):
                continue
            if self._is_compositor_peer(peer_id):
                continue
            stage_roster.add(peer_id)
            display_name = peer.get('displayName')
            if isinstance(display_name, str) and display_name.strip():
                self._display_names[peer_id] = display_name.strip()

        # Fallback when older mediasoup builds omit joinedPeers: keep anyone with producers.
        if not joined_peers:
            stage_roster |= set(producers_by_peer.keys())

        with self._lock:
            # Hard detach peers that left the room.
            for peer_id in list(self._participants.keys()):
                if peer_id not in stage_roster:
                    participant = self._participants.pop(peer_id)
                    self._video_missing_since.pop(peer_id, None)
                    self._consumer_service.detach_participant(participant)

            for peer_id in stage_roster:
                peer_info = producers_by_peer.get(peer_id, {'peerId': peer_id, 'producers': []})
                audio_id, video_id = self._extract_av_producers(peer_info)
                display_name = self._display_names.get(peer_id, peer_id)
                current = self._participants.get(peer_id)

                if current is None:
                    if audio_id and video_id:
                        try:
                            participant = self._consumer_service.attach_participant(
                                peer_id,
                                audio_id,
                                video_id,
                            )
                            participant.display_name = display_name
                            self._participants[peer_id] = participant
                            self._video_missing_since.pop(peer_id, None)
                        except Exception:
                            logger.exception(
                                'Failed to attach ingest for participant %s',
                                peer_id,
                            )
                    continue

                # Already attached — live video present (webcam or screenshare).
                if video_id:
                    self._video_missing_since.pop(peer_id, None)
                    needs_reattach = (
                        current.audio_producer_id != audio_id
                        or current.video_producer_id != video_id
                        or current.video_mode == 'placeholder'
                        or audio_id is None
                    )
                    if needs_reattach and audio_id and video_id:
                        logger.info(
                            'Re-attaching ingest for %s (live video restore/replace)',
                            peer_id,
                        )
                        self._consumer_service.detach_participant(current)
                        del self._participants[peer_id]
                        try:
                            participant = self._consumer_service.attach_participant(
                                peer_id,
                                audio_id,
                                video_id,
                            )
                            participant.display_name = display_name
                            self._participants[peer_id] = participant
                        except Exception:
                            logger.exception(
                                'Failed to re-attach ingest for participant %s',
                                peer_id,
                            )
                    continue

                # No video producer while sticky (webcam disabled).
                if current.video_mode == 'placeholder':
                    continue

                # Soft-disable as soon as the webcam producer is gone so the mix
                # never freezes on a dead RTP pad (grace only delays the swap and
                # starved the RTMP encoder). Scene camera switch may flash a
                # placeholder for one poll interval, then re-attach when video
                # returns.
                grace = _video_soft_disable_grace_sec()
                missing_since = self._video_missing_since.get(peer_id)
                now = time.monotonic()
                if missing_since is None:
                    self._video_missing_since[peer_id] = now
                    if grace > 0:
                        logger.info(
                            'Webcam missing for sticky peer %s; grace %.1fs before placeholder',
                            peer_id,
                            grace,
                        )
                        continue
                elif grace > 0 and now - missing_since < grace:
                    continue

                try:
                    self._consumer_service.soft_disable_video(
                        current,
                        display_name=display_name,
                    )
                    self._video_missing_since.pop(peer_id, None)
                except Exception:
                    logger.exception(
                        'Failed to soft-disable video for participant %s',
                        peer_id,
                    )

    def _is_compositor_peer(self, peer_id: str) -> bool:
        return peer_id == self.compositor_peer_id or peer_id.startswith('compositor-')

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
        destination_urls: list[str] | None = None,
        output_dir=None,
    ) -> None:
        self._compositor_pipeline.start_streaming(
            destination_type=destination_type,
            destination_url=destination_url,
            destination_urls=destination_urls,
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
                streaming_destination_urls=pipeline_status.streaming_destination_urls,
                video_backend=pipeline_status.video_backend,
                requested_video_backend=pipeline_status.requested_video_backend,
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

