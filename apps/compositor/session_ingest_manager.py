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
from apps.compositor.tile_order_sync import (
    apply_tile_order_to_pipeline,
    build_set_tile_order_command,
)
from apps.sessions.models import StudioSession
from integrations.mediasoup.client import MediasoupHttpClient

logger = logging.getLogger(__name__)


def _video_soft_disable_grace_sec() -> float:
    return float(getattr(settings, 'VIDEO_SOFT_DISABLE_GRACE_SEC', 0.0))


@dataclass(frozen=True)
class VideoProducerRef:
    producer_id: str
    source: str
    source_id: str | None = None


@dataclass(frozen=True)
class AudioProducerRef:
    producer_id: str
    source_id: str | None = None


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
class UriSourceIngestStatus:
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
    uri_sources: list[UriSourceIngestStatus] = field(default_factory=list)


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
        self._uri_sources: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()
        self._stopped = False
        # seat_id → monotonic time when video producer first went missing
        self._video_missing_since: dict[str, float] = {}
        self._display_names: dict[str, str] = {}
        # seat_id → monotonic deadline; skip attach retries until then
        self._attach_backoff_until: dict[str, float] = {}
        # Source ids on the active scene (visible or eye-hidden). None = unknown
        # (do not prune idle seats until first SetTileOrder).
        self._scene_retained_source_ids: frozenset[str] | None = None

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
        compositor_pipeline.set_sfu_context(
            client,
            room_id=room_id,
            compositor_peer_id=compositor_peer_id,
        )

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

        manager = cls(
            session_id=str(session.id),
            room_id=room_id,
            compositor_peer_id=compositor_peer_id,
            layout=session.layout,
            consumer_service=consumer_service,
            compositor_pipeline=compositor_pipeline,
        )
        # Seed scene retention from the same tile-order snapshot applied to the pipeline.
        tile_cmd = build_set_tile_order_command(session)
        manager.set_tile_order(
            host_peer_id=tile_cmd.host_peer_id,
            slot_assignments=tile_cmd.slot_assignments,
            hidden_source_ids=tile_cmd.hidden_source_ids,
            scene_source_ids=tile_cmd.scene_source_ids,
        )
        return manager

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
        scene_source_ids: list[str] | None = None,
    ) -> None:
        with self._lock:
            if scene_source_ids is not None:
                self._scene_retained_source_ids = frozenset(
                    sid.strip()
                    for sid in scene_source_ids
                    if isinstance(sid, str) and sid.strip()
                )
                self._prune_extra_seats_not_on_scene_unlocked()
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
        Missing video becomes an initials placeholder (grace default 0).
        Video producer changes (scene camera switch) replace live video even when
        the mic is muted. Re-enable / restore uses soft-enable or full re-attach.

        Multi-seat: producers with appData.sourceId become extra seats keyed by
        that sourceId (camera-/screen-/prerecorded-*). Primary peer seat stays
        peer_id for the main video without sourceId (legacy guests).
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
            # Hard detach seats whose owning peer left the room.
            for seat_id in list(self._participants.keys()):
                participant = self._participants[seat_id]
                owner = self._seat_owner(seat_id, participant)
                if owner not in stage_roster:
                    self._participants.pop(seat_id)
                    self._video_missing_since.pop(seat_id, None)
                    self._consumer_service.detach_participant(participant)

            for peer_id in stage_roster:
                peer_info = producers_by_peer.get(peer_id, {'peerId': peer_id, 'producers': []})
                audio_id, extra_audios, videos = self._extract_av_producers(peer_info)
                primary_video, extra_videos = self._split_primary_and_extra(videos)
                display_name = self._display_names.get(peer_id, peer_id)
                primary_video_id = primary_video.producer_id if primary_video else None

                self._sync_primary_seat(
                    peer_id=peer_id,
                    audio_id=audio_id,
                    video_id=primary_video_id,
                    display_name=display_name,
                )
                self._sync_extra_seats(
                    peer_id=peer_id,
                    extra_videos=extra_videos,
                    extra_audios=extra_audios,
                    display_name=display_name,
                )

    def _sync_primary_seat(
        self,
        *,
        peer_id: str,
        audio_id: str | None,
        video_id: str | None,
        display_name: str,
    ) -> None:
        current = self._participants.get(peer_id)

        if current is None:
            if audio_id and video_id:
                if self._attach_in_backoff(peer_id):
                    return
                try:
                    participant = self._consumer_service.attach_participant(
                        peer_id,
                        audio_id,
                        video_id,
                        owner_peer_id=peer_id,
                    )
                    participant.display_name = display_name
                    self._participants[peer_id] = participant
                    self._video_missing_since.pop(peer_id, None)
                    self._clear_attach_backoff(peer_id)
                except Exception as exc:
                    self._note_attach_failure(peer_id, exc)
                    logger.exception(
                        'Failed to attach ingest for participant %s',
                        peer_id,
                    )
            return

        # Already attached — live video present (webcam or screenshare).
        if video_id:
            self._video_missing_since.pop(peer_id, None)

            video_changed = (
                current.video_mode == 'placeholder'
                or current.video_producer_id != video_id
            )
            audio_changed = current.audio_producer_id != audio_id

            if video_changed:
                # Scene camera switch often returns a new video producer while
                # still in rtp mode (before grace soft-disable). With mic muted
                # there is no audio_id — soft-enable replaces the dead/old
                # video pad without requiring a full A/V re-attach.
                if (
                    audio_id
                    and current.video_mode != 'placeholder'
                ):
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
                            owner_peer_id=peer_id,
                        )
                        participant.display_name = display_name
                        self._participants[peer_id] = participant
                    except Exception:
                        logger.exception(
                            'Failed to re-attach ingest for participant %s',
                            peer_id,
                        )
                else:
                    try:
                        self._consumer_service.soft_enable_video(
                            current,
                            video_id,
                            display_name=display_name,
                        )
                        if audio_id:
                            current.audio_producer_id = audio_id
                    except Exception:
                        logger.exception(
                            'Failed to soft-enable video for participant %s',
                            peer_id,
                        )
                return

            if audio_changed and audio_id:
                logger.info(
                    'Re-attaching ingest for %s (audio producer changed)',
                    peer_id,
                )
                self._consumer_service.detach_participant(current)
                del self._participants[peer_id]
                try:
                    participant = self._consumer_service.attach_participant(
                        peer_id,
                        audio_id,
                        video_id,
                        owner_peer_id=peer_id,
                    )
                    participant.display_name = display_name
                    self._participants[peer_id] = participant
                except Exception:
                    logger.exception(
                        'Failed to re-attach ingest for participant %s',
                        peer_id,
                    )
            return

        # No primary video producer while sticky (webcam disabled).
        if current.video_mode == 'placeholder':
            return

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
                return
        elif grace > 0 and now - missing_since < grace:
            return

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

    def _sync_extra_seats(
        self,
        *,
        peer_id: str,
        extra_videos: list[VideoProducerRef],
        extra_audios: dict[str, str],
        display_name: str,
    ) -> None:
        desired: dict[str, VideoProducerRef] = {}
        for video in extra_videos:
            if not video.source_id:
                continue
            desired[video.source_id] = video

        # Extra seats no longer published: keep idle placeholder while still on
        # the active scene (Wave C); hard-detach when detached from the scene.
        for seat_id in list(self._participants.keys()):
            participant = self._participants[seat_id]
            if self._seat_owner(seat_id, participant) != peer_id:
                continue
            source_id = getattr(participant, 'source_id', None)
            if not isinstance(source_id, str) or not source_id:
                continue
            if seat_id in desired:
                continue
            if self._should_prune_extra_seat(seat_id):
                self._participants.pop(seat_id)
                self._video_missing_since.pop(seat_id, None)
                self._consumer_service.detach_participant(participant)
                continue
            if participant.video_mode == 'placeholder':
                continue
            try:
                label = self._extra_seat_placeholder_label(seat_id, display_name)
                self._consumer_service.soft_disable_video(
                    participant,
                    display_name=label,
                )
                # Screen/system audio dies with the share; drop producer id so
                # restore does not treat stale audio as still attached.
                participant.audio_producer_id = None
                self._video_missing_since.pop(seat_id, None)
            except Exception:
                logger.exception(
                    'Failed to soft-disable idle extra seat %s for peer %s',
                    seat_id,
                    peer_id,
                )

        for seat_id, video in desired.items():
            current = self._participants.get(seat_id)
            seat_name = display_name
            audio_id = extra_audios.get(seat_id)

            if current is None:
                if self._attach_in_backoff(seat_id):
                    continue
                try:
                    participant = self._attach_extra_seat(
                        seat_id=seat_id,
                        peer_id=peer_id,
                        video_id=video.producer_id,
                        audio_id=audio_id,
                        display_name=seat_name,
                    )
                    self._participants[seat_id] = participant
                    self._clear_attach_backoff(seat_id)
                except Exception as exc:
                    self._note_attach_failure(seat_id, exc)
                    logger.exception(
                        'Failed to attach extra seat %s for peer %s',
                        seat_id,
                        peer_id,
                    )
                continue

            if current.video_mode == 'placeholder':
                try:
                    if audio_id:
                        # Placeholder seats have no live audio branch to reuse.
                        self._consumer_service.detach_participant(current)
                        del self._participants[seat_id]
                        participant = self._attach_extra_seat(
                            seat_id=seat_id,
                            peer_id=peer_id,
                            video_id=video.producer_id,
                            audio_id=audio_id,
                            display_name=seat_name,
                        )
                        self._participants[seat_id] = participant
                    else:
                        self._consumer_service.soft_enable_video(
                            current,
                            video.producer_id,
                            display_name=seat_name,
                        )
                    self._video_missing_since.pop(seat_id, None)
                except Exception:
                    logger.exception(
                        'Failed to restore live video for extra seat %s',
                        seat_id,
                    )
                continue

            video_same = (
                current.video_producer_id == video.producer_id
                and current.video_mode == 'rtp'
            )
            audio_same = current.audio_producer_id == audio_id
            # Video-only seats store audio_producer_id as None; treat missing
            # source audio the same way.
            if audio_id is None and not current.audio_producer_id:
                audio_same = True

            if video_same and audio_same:
                continue

            # Replace when video/audio producer set changes (incl. audio appear/disappear).
            try:
                self._consumer_service.detach_participant(current)
                del self._participants[seat_id]
                participant = self._attach_extra_seat(
                    seat_id=seat_id,
                    peer_id=peer_id,
                    video_id=video.producer_id,
                    audio_id=audio_id,
                    display_name=seat_name,
                )
                self._participants[seat_id] = participant
            except Exception:
                logger.exception(
                    'Failed to replace extra seat %s for peer %s',
                    seat_id,
                    peer_id,
                )

    def _should_prune_extra_seat(self, seat_id: str) -> bool:
        retained = self._scene_retained_source_ids
        if retained is None:
            return False
        return seat_id not in retained

    def _prune_extra_seats_not_on_scene_unlocked(self) -> None:
        retained = self._scene_retained_source_ids
        if retained is None:
            return
        for seat_id in list(self._participants.keys()):
            participant = self._participants[seat_id]
            source_id = getattr(participant, 'source_id', None)
            if not isinstance(source_id, str) or not source_id:
                continue
            if seat_id in retained:
                continue
            self._participants.pop(seat_id)
            self._video_missing_since.pop(seat_id, None)
            try:
                self._consumer_service.detach_participant(participant)
            except Exception:
                logger.exception(
                    'Failed to prune extra seat %s after scene detach',
                    seat_id,
                )

    @staticmethod
    def _extra_seat_placeholder_label(seat_id: str, owner_display_name: str) -> str:
        if seat_id.startswith('screen'):
            return 'Screen'
        if seat_id.startswith('camera'):
            return 'Camera'
        return owner_display_name or seat_id

    def _attach_extra_seat(
        self,
        *,
        seat_id: str,
        peer_id: str,
        video_id: str,
        audio_id: str | None,
        display_name: str,
    ) -> ParticipantIngest:
        """Attach host-owned extra seat as A/V when source audio exists, else video-only."""
        if audio_id:
            participant = self._consumer_service.attach_participant(
                seat_id,
                audio_id,
                video_id,
                owner_peer_id=peer_id,
                source_id=seat_id,
                host_owned=True,
            )
            participant.display_name = display_name
            return participant

        return self._consumer_service.attach_video_seat(
            seat_id,
            video_id,
            owner_peer_id=peer_id,
            source_id=seat_id,
            display_name=display_name,
            host_owned=True,
        )

    def _is_compositor_peer(self, peer_id: str) -> bool:
        return peer_id == self.compositor_peer_id or peer_id.startswith('compositor-')

    def _attach_in_backoff(self, seat_id: str) -> bool:
        until = self._attach_backoff_until.get(seat_id)
        return until is not None and time.monotonic() < until

    def _note_attach_failure(self, seat_id: str, exc: BaseException) -> None:
        message = str(exc)
        # Port exhaustion needs a longer cool-down so we don't burn the pool.
        delay = 30.0 if 'No free RTP ports' in message else 5.0
        self._attach_backoff_until[seat_id] = time.monotonic() + delay
        logger.warning(
            'Attach backoff %.0fs for seat %s (%s)',
            delay,
            seat_id,
            message.splitlines()[0] if message else type(exc).__name__,
        )

    def _clear_attach_backoff(self, seat_id: str) -> None:
        self._attach_backoff_until.pop(seat_id, None)

    @staticmethod
    def _seat_owner(seat_id: str, participant: ParticipantIngest) -> str:
        owner = getattr(participant, 'owner_peer_id', None)
        if isinstance(owner, str) and owner:
            return owner
        return seat_id

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

    def add_uri_video_source(
        self,
        source_id: str,
        *,
        url: str,
        display_name: str = '',
        produce_to_sfu: bool = True,
        loop: bool | None = True,
    ) -> None:
        self._compositor_pipeline.add_uri_video_source(
            source_id,
            url=url,
            display_name=display_name,
            produce_to_sfu=produce_to_sfu,
            loop=loop,
        )
        # URI SFU egress joins the compositor peer; keep ConsumerService in sync
        # so later camera attaches do not POST /join again and hit 409.
        if produce_to_sfu:
            self._consumer_service.mark_joined()
        with self._lock:
            self._uri_sources[source_id] = {
                'url': url,
                'display_name': display_name,
            }

    def remove_uri_video_source(self, source_id: str) -> None:
        self._compositor_pipeline.remove_uri_video_source(source_id)
        with self._lock:
            self._uri_sources.pop(source_id, None)

    def update_uri_video_playback(
        self,
        source_id: str,
        *,
        action: str,
        position_ms: float | None = None,
        loop: bool | None = None,
        volume: float | None = None,
        muted: bool | None = None,
    ) -> None:
        self._compositor_pipeline.update_uri_video_playback(
            source_id,
            action=action,
            position_ms=position_ms,
            loop=loop,
            volume=volume,
            muted=muted,
        )

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            for source_id in list(self._uri_sources.keys()):
                self._compositor_pipeline.remove_uri_video_source(source_id)
            self._uri_sources.clear()
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
                        audio_producer_id=participant.audio_producer_id or '',
                        video_producer_id=participant.video_producer_id,
                        audio_port=(
                            participant.ports.audio.rtp_port
                            if participant.ports.audio is not None
                            else 0
                        ),
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

            uri_sources = []
            for source_id, meta in self._uri_sources.items():
                stats = self._compositor_pipeline.get_uri_source_stats(source_id)
                uri_sources.append(
                    UriSourceIngestStatus(
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
                uri_sources=uri_sources,
            )

    @staticmethod
    def _extract_av_producers(
        peer_info: dict[str, Any],
    ) -> tuple[str | None, dict[str, str], list[VideoProducerRef]]:
        """
        Split producers into primary mic audio, sourceId-tagged audios, and videos.

        Primary mic: kind=audio, source=audio, no sourceId (host/guest seat).
        Extra audio: kind=audio, source=audio, with sourceId (e.g. screen share).
        """
        audio_id: str | None = None
        extra_audios: dict[str, str] = {}
        videos: list[VideoProducerRef] = []

        for producer in peer_info.get('producers', []):
            kind = producer.get('kind')
            source = producer.get('source')

            if kind == 'audio' and source == 'audio':
                raw_source_id = producer.get('sourceId') or producer.get('source_id')
                source_id = (
                    str(raw_source_id).strip()
                    if isinstance(raw_source_id, str) and raw_source_id.strip()
                    else None
                )
                if source_id:
                    extra_audios[source_id] = producer['producerId']
                else:
                    audio_id = producer['producerId']
            elif kind == 'video' and source in ('video', 'screensharing'):
                raw_source_id = producer.get('sourceId') or producer.get('source_id')
                source_id = (
                    str(raw_source_id).strip()
                    if isinstance(raw_source_id, str) and raw_source_id.strip()
                    else None
                )
                videos.append(
                    VideoProducerRef(
                        producer_id=producer['producerId'],
                        source=str(source),
                        source_id=source_id,
                    )
                )

        return audio_id, extra_audios, videos

    @staticmethod
    def _split_primary_and_extra(
        videos: list[VideoProducerRef],
    ) -> tuple[VideoProducerRef | None, list[VideoProducerRef]]:
        primary: VideoProducerRef | None = None
        extras: list[VideoProducerRef] = []
        for video in videos:
            if video.source_id:
                extras.append(video)
                continue
            if primary is None:
                primary = video
            elif video.source == 'video' and primary.source != 'video':
                primary = video
            elif video.source == primary.source:
                # Legacy last-wins among same source type without sourceId.
                primary = video
        return primary, extras
