"""GStreamer compositor pipeline mixing participant streams into one canvas."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

from apps.compositor.ingest_branch import IngestStats
from apps.compositor.pipeline_bus_monitor import PipelineBusMonitor
from apps.layouts.manager import LayoutManager
from apps.layouts.strategies.base import Size, TileConfig
from apps.recording.gstreamer_recorder import (
    RecordingBranch,
    attach_to_tees,
    build_recording_branch,
    finalize_recording,
    teardown_recording_branch,
)
from apps.streaming.gstreamer_streamer import (
    StreamingBranch,
    attach_stream_to_tees,
    build_streaming_branch,
    finalize_hls_stream,
    teardown_streaming_branch,
)
from apps.streaming.models import DestinationType
from apps.sessions.models import LayoutType
from core import events
from core.webhooks import emit_event

logger = logging.getLogger(__name__)

Gst.init(None)


@dataclass
class ParticipantBranch:
    participant_peer_id: str
    compositor_sink_pad: Gst.Pad
    mixer_sink_pad: Gst.Pad
    elements: list[Gst.Element] = field(default_factory=list)
    stats: IngestStats = field(default_factory=IngestStats)


@dataclass
class CompositorPipelineStatus:
    layout: str
    canvas_width: int
    canvas_height: int
    composited_frames: int
    participant_ids: list[str]
    host_peer_id: str | None
    recording_active: bool
    recording_file_path: str | None
    streaming_active: bool
    streaming_destination_type: str | None
    streaming_destination_url: str | None


class CompositorPipeline:
    """
    Session-level GStreamer pipeline with compositor + audiomixer.

    Each participant RTP stream is decoded and mixed into a single canvas output.
    """

    def __init__(
        self,
        session_id: str,
        *,
        width: int,
        height: int,
        fps: int,
        layout: str = LayoutType.CONTAIN,
    ) -> None:
        self.session_id = session_id
        self.width = width
        self.height = height
        self.fps = fps
        self._layout = layout
        self._host_peer_id: str | None = None
        self._layout_manager = LayoutManager.for_layout(
            layout,
            Size(width=width, height=height),
        )
        self._pipeline: Gst.Pipeline | None = None
        self._compositor: Gst.Element | None = None
        self._audiomixer: Gst.Element | None = None
        self._video_tee: Gst.Element | None = None
        self._audio_tee: Gst.Element | None = None
        self._participants: dict[str, ParticipantBranch] = {}
        self._recording: RecordingBranch | None = None
        self._streaming: StreamingBranch | None = None
        self._streaming_config: tuple[str, str, Path | None] | None = None
        self._stream_monitor: PipelineBusMonitor | None = None
        self._stream_reconnect_attempts = 0
        self._on_stream_permanent_failure: Callable[[str], None] | None = None
        self._composited_frames = 0
        self._lock = threading.Lock()

    def set_stream_failure_handler(self, handler: Callable[[str], None] | None) -> None:
        self._on_stream_permanent_failure = handler

    def start(self) -> None:
        with self._lock:
            if self._pipeline is not None:
                return

            pipeline = Gst.Pipeline.new(f'compositor-{self.session_id}')
            compositor = Gst.ElementFactory.make('compositor', 'mix')
            capsfilter = Gst.ElementFactory.make('capsfilter', 'out_caps')
            convert = Gst.ElementFactory.make('videoconvert', 'out_convert')
            video_tee = Gst.ElementFactory.make('tee', 'video_tee')
            video_queue = Gst.ElementFactory.make('queue', 'video_monitor_queue')
            sink = Gst.ElementFactory.make('fakesink', 'video_out')
            audiomixer = Gst.ElementFactory.make('audiomixer', 'amix')
            audio_convert = Gst.ElementFactory.make('audioconvert', 'audio_convert')
            audio_resample = Gst.ElementFactory.make('audioresample', 'audio_resample')
            audio_capsfilter = Gst.ElementFactory.make('capsfilter', 'audio_out_caps')
            audio_tee = Gst.ElementFactory.make('tee', 'audio_tee')
            audio_queue = Gst.ElementFactory.make('queue', 'audio_monitor_queue')
            audio_sink = Gst.ElementFactory.make('fakesink', 'audio_out')

            if not all(
                [
                    pipeline,
                    compositor,
                    capsfilter,
                    convert,
                    video_tee,
                    video_queue,
                    sink,
                    audiomixer,
                    audio_convert,
                    audio_resample,
                    audio_capsfilter,
                    audio_tee,
                    audio_queue,
                    audio_sink,
                ]
            ):
                raise RuntimeError('Failed to create compositor pipeline elements')

            capsfilter.set_property(
                'caps',
                Gst.Caps.from_string(
                    f'video/x-raw,width={self.width},height={self.height},'
                    f'framerate={self.fps}/1'
                ),
            )
            audio_capsfilter.set_property(
                'caps',
                Gst.Caps.from_string('audio/x-raw,rate=48000,channels=2'),
            )
            sink.set_property('sync', False)
            audio_sink.set_property('sync', False)

            for element in (
                compositor,
                capsfilter,
                convert,
                video_tee,
                video_queue,
                sink,
                audiomixer,
                audio_convert,
                audio_resample,
                audio_capsfilter,
                audio_tee,
                audio_queue,
                audio_sink,
            ):
                pipeline.add(element)

            if not compositor.link(capsfilter):
                raise RuntimeError('Failed to link compositor -> capsfilter')
            if not capsfilter.link(convert):
                raise RuntimeError('Failed to link capsfilter -> videoconvert')
            if not convert.link(video_tee):
                raise RuntimeError('Failed to link videoconvert -> video tee')

            video_tee_src = video_tee.get_request_pad('src_%u')
            if video_tee_src is None:
                raise RuntimeError('Failed to request video tee src pad')
            if video_tee_src.link(video_queue.get_static_pad('sink')) != Gst.PadLinkReturn.OK:
                raise RuntimeError('Failed to link video tee -> queue')
            if not video_queue.link(sink):
                raise RuntimeError('Failed to link video queue -> fakesink')
            if not audiomixer.link(audio_convert):
                raise RuntimeError('Failed to link audiomixer -> audioconvert')
            if not audio_convert.link(audio_resample):
                raise RuntimeError('Failed to link audioconvert -> audioresample')
            if not audio_resample.link(audio_capsfilter):
                raise RuntimeError('Failed to link audioresample -> audio capsfilter')
            if not audio_capsfilter.link(audio_tee):
                raise RuntimeError('Failed to link audio capsfilter -> audio tee')

            audio_tee_src = audio_tee.get_request_pad('src_%u')
            if audio_tee_src is None:
                raise RuntimeError('Failed to request audio tee src pad')
            if audio_tee_src.link(audio_queue.get_static_pad('sink')) != Gst.PadLinkReturn.OK:
                raise RuntimeError('Failed to link audio tee -> queue')
            if not audio_queue.link(audio_sink):
                raise RuntimeError('Failed to link audio queue -> fakesink')

            sink_pad = sink.get_static_pad('sink')
            sink_pad.add_probe(Gst.PadProbeType.BUFFER, self._on_composited_buffer, None)

            ret = pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('Failed to start compositor pipeline')

            self._pipeline = pipeline
            self._compositor = compositor
            self._audiomixer = audiomixer
            self._video_tee = video_tee
            self._audio_tee = audio_tee

            logger.info(
                'Compositor pipeline started for session %s (%sx%s @ %sfps, layout=%s)',
                self.session_id,
                self.width,
                self.height,
                self.fps,
                self._layout,
            )

    def stop(self) -> None:
        with self._lock:
            if self._streaming is not None:
                try:
                    self._stop_streaming_unlocked()
                except Exception:
                    logger.exception(
                        'Failed to stop streaming while stopping session %s',
                        self.session_id,
                    )
                    if (
                        self._pipeline is not None
                        and self._video_tee is not None
                        and self._audio_tee is not None
                    ):
                        teardown_streaming_branch(
                            self._streaming,
                            self._pipeline,
                            video_tee=self._video_tee,
                            audio_tee=self._audio_tee,
                        )
                    self._streaming = None

            if self._recording is not None:
                try:
                    self._stop_recording_unlocked()
                except Exception:
                    logger.exception(
                        'Failed to finalize recording while stopping session %s',
                        self.session_id,
                    )
                    if self._pipeline is not None and self._video_tee is not None and self._audio_tee is not None:
                        teardown_recording_branch(
                            self._recording,
                            self._pipeline,
                            video_tee=self._video_tee,
                            audio_tee=self._audio_tee,
                        )
                    self._recording = None

            for participant_id in list(self._participants.keys()):
                self._remove_participant_unlocked(participant_id)

            if self._pipeline is not None:
                self._pipeline.set_state(Gst.State.NULL)
                self._pipeline = None
                self._compositor = None
                self._audiomixer = None
                self._video_tee = None
                self._audio_tee = None

    def start_recording(self, file_path: Path) -> None:
        with self._lock:
            if self._pipeline is None or self._video_tee is None or self._audio_tee is None:
                raise RuntimeError('Compositor pipeline is not started')

            if self._recording is not None:
                raise RuntimeError('Recording is already active')

            branch = build_recording_branch(
                file_path=file_path,
                video_bitrate=settings.RECORDING_VIDEO_BITRATE,
                audio_bitrate=settings.RECORDING_AUDIO_BITRATE,
            )

            for element in branch.elements:
                self._pipeline.add(element)

            attach_to_tees(
                branch,
                video_tee=self._video_tee,
                audio_tee=self._audio_tee,
            )

            for element in branch.elements:
                element.sync_state_with_parent()

            self._recording = branch
            logger.info('Recording started for session %s -> %s', self.session_id, file_path)

    def stop_recording(self) -> Path:
        with self._lock:
            return self._stop_recording_unlocked()

    def is_recording(self) -> bool:
        with self._lock:
            return self._recording is not None

    def start_streaming(
        self,
        *,
        destination_type: str,
        destination_url: str,
        output_dir: Path | None = None,
    ) -> None:
        with self._lock:
            if self._pipeline is None or self._video_tee is None or self._audio_tee is None:
                raise RuntimeError('Compositor pipeline is not started')

            if self._streaming is not None:
                raise RuntimeError('Streaming is already active')

            branch = build_streaming_branch(
                destination_type=destination_type,
                destination_url=destination_url,
                output_dir=output_dir,
                video_bitrate=settings.STREAMING_VIDEO_BITRATE,
                audio_bitrate=settings.STREAMING_AUDIO_BITRATE,
            )

            for element in branch.elements:
                self._pipeline.add(element)

            attach_stream_to_tees(
                branch,
                video_tee=self._video_tee,
                audio_tee=self._audio_tee,
            )

            for element in branch.elements:
                element.sync_state_with_parent()

            self._streaming = branch
            self._streaming_config = (destination_type, destination_url, output_dir)
            self._stream_reconnect_attempts = 0

            if destination_type == DestinationType.RTMP:
                self._start_stream_monitor()

            logger.info(
                'Streaming started for session %s (%s -> %s)',
                self.session_id,
                destination_type,
                destination_url,
            )

    def stop_streaming(self) -> None:
        with self._lock:
            self._stop_streaming_unlocked()

    def is_streaming(self) -> bool:
        with self._lock:
            return self._streaming is not None

    def add_participant(
        self,
        participant_peer_id: str,
        *,
        audio_port: int,
        video_port: int,
        audio_payload_type: int,
        video_payload_type: int,
    ) -> IngestStats:
        with self._lock:
            if self._pipeline is None:
                raise RuntimeError('Compositor pipeline is not started')

            if participant_peer_id in self._participants:
                return self._participants[participant_peer_id].stats

            branch = self._build_participant_branch(
                participant_peer_id=participant_peer_id,
                audio_port=audio_port,
                video_port=video_port,
                audio_payload_type=audio_payload_type,
                video_payload_type=video_payload_type,
            )
            self._participants[participant_peer_id] = branch

            if self._host_peer_id is None:
                self._host_peer_id = participant_peer_id

            self._apply_layout_unlocked()
            return branch.stats

    def remove_participant(self, participant_peer_id: str) -> None:
        with self._lock:
            self._remove_participant_unlocked(participant_peer_id)

            if self._host_peer_id == participant_peer_id:
                self._host_peer_id = next(iter(self._participants), None)

            self._apply_layout_unlocked()

    def set_layout(self, layout: str) -> None:
        with self._lock:
            self._layout = layout
            self._layout_manager.set_strategy(layout)
            self._apply_layout_unlocked()

    def get_participant_stats(self, participant_peer_id: str) -> IngestStats | None:
        branch = self._participants.get(participant_peer_id)
        return branch.stats if branch else None

    def get_status(self) -> CompositorPipelineStatus:
        with self._lock:
            recording_path = (
                str(self._recording.file_path) if self._recording is not None else None
            )
            return CompositorPipelineStatus(
                layout=self._layout,
                canvas_width=self.width,
                canvas_height=self.height,
                composited_frames=self._composited_frames,
                participant_ids=list(self._participants.keys()),
                host_peer_id=self._host_peer_id,
                recording_active=self._recording is not None,
                recording_file_path=recording_path,
                streaming_active=self._streaming is not None,
                streaming_destination_type=(
                    self._streaming.destination_type if self._streaming else None
                ),
                streaming_destination_url=(
                    self._streaming.destination_url if self._streaming else None
                ),
            )

    def _stop_streaming_unlocked(self) -> None:
        if self._streaming is None or self._pipeline is None:
            raise RuntimeError('No active stream')

        self._teardown_streaming_unlocked()

        logger.info(
            'Streaming stopped for session %s',
            self.session_id,
        )

    def _teardown_streaming_unlocked(self) -> None:
        if self._streaming is None or self._pipeline is None:
            return

        self._stop_stream_monitor()
        branch = self._streaming
        destination_url = branch.destination_url

        try:
            finalize_hls_stream(
                branch,
                self._pipeline,
                timeout_sec=settings.STREAMING_EOS_TIMEOUT_SEC,
            )
        finally:
            assert self._video_tee is not None
            assert self._audio_tee is not None
            teardown_streaming_branch(
                branch,
                self._pipeline,
                video_tee=self._video_tee,
                audio_tee=self._audio_tee,
            )
            self._streaming = None
            self._streaming_config = None
            self._stream_reconnect_attempts = 0

        logger.debug(
            'Streaming branch removed for session %s (%s)',
            self.session_id,
            destination_url,
        )

    def _start_stream_monitor(self) -> None:
        if self._pipeline is None or self._streaming is None:
            return

        self._stop_stream_monitor()
        watched = set(self._streaming.elements)
        self._stream_monitor = PipelineBusMonitor(
            self._pipeline,
            watched_elements=watched,
            on_error=self._handle_stream_error,
        )
        self._stream_monitor.start()

    def _stop_stream_monitor(self) -> None:
        if self._stream_monitor is not None:
            self._stream_monitor.stop()
            self._stream_monitor = None

    def _handle_stream_error(self, error_message: str) -> None:
        logger.warning(
            'Stream error for session %s: %s',
            self.session_id,
            error_message,
        )

        with self._lock:
            if self._streaming is None or self._streaming_config is None:
                return

            if self._streaming.destination_type != DestinationType.RTMP:
                return

            max_attempts = settings.STREAMING_RTMP_MAX_RECONNECT_ATTEMPTS
            if self._stream_reconnect_attempts >= max_attempts:
                self._fail_stream_permanently(error_message)
                return

            self._stream_reconnect_attempts += 1
            attempt = self._stream_reconnect_attempts
            destination_url = self._streaming.destination_url

        emit_event(
            events.STREAM_RECONNECTING,
            {
                'session_id': self.session_id,
                'destination_url': destination_url,
                'attempt': attempt,
                'max_attempts': max_attempts,
                'error': error_message,
            },
        )

        delay = settings.STREAMING_RTMP_RECONNECT_DELAY_SEC
        time.sleep(delay)

        with self._lock:
            if not self._reconnect_streaming_unlocked():
                self._fail_stream_permanently(error_message)
                return

        emit_event(
            events.STREAM_RECONNECTED,
            {
                'session_id': self.session_id,
                'destination_url': destination_url,
                'attempt': attempt,
            },
        )
        logger.info(
            'RTMP stream reconnected for session %s (attempt %s)',
            self.session_id,
            attempt,
        )

    def _reconnect_streaming_unlocked(self) -> bool:
        if (
            self._pipeline is None
            or self._video_tee is None
            or self._audio_tee is None
            or self._streaming is None
            or self._streaming_config is None
        ):
            return False

        branch = self._streaming
        teardown_streaming_branch(
            branch,
            self._pipeline,
            video_tee=self._video_tee,
            audio_tee=self._audio_tee,
        )
        self._streaming = None
        self._stop_stream_monitor()

        destination_type, destination_url, output_dir = self._streaming_config

        try:
            new_branch = build_streaming_branch(
                destination_type=destination_type,
                destination_url=destination_url,
                output_dir=output_dir,
                video_bitrate=settings.STREAMING_VIDEO_BITRATE,
                audio_bitrate=settings.STREAMING_AUDIO_BITRATE,
            )
            for element in new_branch.elements:
                self._pipeline.add(element)
            attach_stream_to_tees(
                new_branch,
                video_tee=self._video_tee,
                audio_tee=self._audio_tee,
            )
            for element in new_branch.elements:
                element.sync_state_with_parent()
            self._streaming = new_branch
            self._start_stream_monitor()
            return True
        except Exception:
            logger.exception(
                'Failed to reconnect RTMP stream for session %s',
                self.session_id,
            )
            return False

    def _fail_stream_permanently(self, error_message: str) -> None:
        destination_url = self._streaming.destination_url if self._streaming else ''
        self._teardown_streaming_unlocked()

        emit_event(
            events.STREAM_FAILED,
            {
                'session_id': self.session_id,
                'destination_url': destination_url,
                'error': error_message,
            },
        )

        if self._on_stream_permanent_failure is not None:
            self._on_stream_permanent_failure(error_message)

    def _stop_recording_unlocked(self) -> Path:
        if self._recording is None or self._pipeline is None:
            raise RuntimeError('No active recording')

        branch = self._recording
        file_path = branch.file_path

        try:
            finalize_recording(
                branch,
                self._pipeline,
                timeout_sec=settings.RECORDING_EOS_TIMEOUT_SEC,
            )
        finally:
            assert self._video_tee is not None
            assert self._audio_tee is not None
            teardown_recording_branch(
                branch,
                self._pipeline,
                video_tee=self._video_tee,
                audio_tee=self._audio_tee,
            )
            self._recording = None

        logger.info('Recording stopped for session %s -> %s', self.session_id, file_path)
        return file_path

    def _build_participant_branch(
        self,
        *,
        participant_peer_id: str,
        audio_port: int,
        video_port: int,
        audio_payload_type: int,
        video_payload_type: int,
    ) -> ParticipantBranch:
        assert self._pipeline is not None
        assert self._compositor is not None
        assert self._audiomixer is not None

        video_elements = self._build_video_ingest_chain(
            participant_peer_id=participant_peer_id,
            port=video_port,
            payload_type=video_payload_type,
        )
        audio_elements = self._build_audio_ingest_chain(
            participant_peer_id=participant_peer_id,
            port=audio_port,
            payload_type=audio_payload_type,
        )

        for element in video_elements + audio_elements:
            self._pipeline.add(element)

        compositor_sink_pad = self._compositor.get_request_pad('sink_%u')
        if compositor_sink_pad is None:
            raise RuntimeError(f'Failed to request compositor sink pad for {participant_peer_id}')

        mixer_pad = self._audiomixer.get_request_pad('sink_%u')
        if mixer_pad is None:
            raise RuntimeError(f'Failed to request audiomixer sink pad for {participant_peer_id}')

        video_src_pad = video_elements[-1].get_static_pad('src')
        if video_src_pad is None or video_src_pad.link(compositor_sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError(
                f'Failed to link video branch to compositor for {participant_peer_id}'
            )

        audio_src_pad = audio_elements[-1].get_static_pad('src')
        if audio_src_pad is None or audio_src_pad.link(mixer_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError(
                f'Failed to link audio branch to audiomixer for {participant_peer_id}'
            )

        branch = ParticipantBranch(
            participant_peer_id=participant_peer_id,
            compositor_sink_pad=compositor_sink_pad,
            mixer_sink_pad=mixer_pad,
            elements=video_elements + audio_elements,
            stats=IngestStats(),
        )

        video_src_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            self._make_video_probe(branch),
            None,
        )
        audio_src_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            self._make_audio_probe(branch),
            None,
        )

        for element in video_elements + audio_elements:
            element.sync_state_with_parent()

        return branch

    def _build_video_ingest_chain(
        self,
        *,
        participant_peer_id: str,
        port: int,
        payload_type: int,
    ) -> list[Gst.Element]:
        src = Gst.ElementFactory.make('udpsrc', f'video_src_{participant_peer_id}')
        jitter = Gst.ElementFactory.make('rtpjitterbuffer', f'video_jitter_{participant_peer_id}')
        depay = Gst.ElementFactory.make('rtpvp8depay', f'video_depay_{participant_peer_id}')
        decoder = Gst.ElementFactory.make('vp8dec', f'video_dec_{participant_peer_id}')
        convert = Gst.ElementFactory.make('videoconvert', f'video_convert_{participant_peer_id}')
        scale = Gst.ElementFactory.make('videoscale', f'video_scale_{participant_peer_id}')

        if not all([src, jitter, depay, decoder, convert, scale]):
            raise RuntimeError(f'Failed to create video ingest elements for {participant_peer_id}')

        src.set_property('port', port)
        src.set_property(
            'caps',
            Gst.Caps.from_string(
                f'application/x-rtp,media=video,clock-rate=90000,encoding-name=VP8,'
                f'payload={payload_type}'
            ),
        )
        jitter.set_property('latency', 50)
        scale.set_property('add-borders', True)

        elements = [src, jitter, depay, decoder, convert, scale]
        self._link_sequential(elements, label=f'video-{participant_peer_id}')
        return elements

    def _build_audio_ingest_chain(
        self,
        *,
        participant_peer_id: str,
        port: int,
        payload_type: int,
    ) -> list[Gst.Element]:
        src = Gst.ElementFactory.make('udpsrc', f'audio_src_{participant_peer_id}')
        jitter = Gst.ElementFactory.make('rtpjitterbuffer', f'audio_jitter_{participant_peer_id}')
        depay = Gst.ElementFactory.make('rtpopusdepay', f'audio_depay_{participant_peer_id}')
        decoder = Gst.ElementFactory.make('opusdec', f'audio_dec_{participant_peer_id}')
        convert = Gst.ElementFactory.make('audioconvert', f'audio_convert_{participant_peer_id}')
        resample = Gst.ElementFactory.make('audioresample', f'audio_resample_{participant_peer_id}')
        queue = Gst.ElementFactory.make('queue', f'audio_queue_{participant_peer_id}')

        if not all([src, jitter, depay, decoder, convert, resample, queue]):
            raise RuntimeError(f'Failed to create audio ingest elements for {participant_peer_id}')

        src.set_property('port', port)
        src.set_property(
            'caps',
            Gst.Caps.from_string(
                f'application/x-rtp,media=audio,clock-rate=48000,encoding-name=OPUS,'
                f'payload={payload_type}'
            ),
        )
        jitter.set_property('latency', 50)

        elements = [src, jitter, depay, decoder, convert, resample, queue]
        self._link_sequential(elements, label=f'audio-{participant_peer_id}')
        return elements

    def _remove_participant_unlocked(self, participant_peer_id: str) -> None:
        branch = self._participants.pop(participant_peer_id, None)
        if branch is None or self._pipeline is None:
            return

        for element in branch.elements:
            element.set_state(Gst.State.NULL)
            self._pipeline.remove(element)

        if self._compositor is not None:
            self._compositor.release_request_pad(branch.compositor_sink_pad)

        if self._audiomixer is not None:
            self._audiomixer.release_request_pad(branch.mixer_sink_pad)

    def _apply_layout_unlocked(self) -> None:
        if self._compositor is None:
            return

        tiles = self._layout_manager.compute_tiles(
            list(self._participants.keys()),
            host_source_id=self._host_peer_id,
        )
        tile_map = {tile.source_id: tile for tile in tiles}

        for participant_id, branch in self._participants.items():
            tile = tile_map.get(participant_id)
            if tile is None:
                continue
            self._apply_tile_to_pad(branch.compositor_sink_pad, tile)

    @staticmethod
    def _apply_tile_to_pad(pad: Gst.Pad, tile: TileConfig) -> None:
        pad.set_property('xpos', tile.x)
        pad.set_property('ypos', tile.y)
        pad.set_property('width', tile.width)
        pad.set_property('height', tile.height)
        pad.set_property('zorder', tile.zorder)
        pad.set_property('alpha', 1.0)

    @staticmethod
    def _link_sequential(elements: list[Gst.Element], *, label: str) -> None:
        for upstream, downstream in zip(elements, elements[1:]):
            if not upstream.link(downstream):
                raise RuntimeError(
                    f'Failed to link {upstream.name} -> {downstream.name} ({label})'
                )

    def _on_composited_buffer(
        self,
        pad: Gst.Pad,
        info: Gst.PadProbeInfo,
        _user_data,
    ) -> Gst.PadProbeReturn:
        self._composited_frames += 1
        return Gst.PadProbeReturn.OK

    @staticmethod
    def _make_video_probe(branch: ParticipantBranch):
        def _probe(pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            branch.stats.video_buffers += 1
            return Gst.PadProbeReturn.OK

        return _probe

    @staticmethod
    def _make_audio_probe(branch: ParticipantBranch):
        def _probe(pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            branch.stats.audio_buffers += 1
            return Gst.PadProbeReturn.OK

        return _probe
