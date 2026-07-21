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
from apps.layouts.types import ScaleMode
from apps.recording.gstreamer_recorder import (
    RecordingBranch,
    build_recording_branch,
    finalize_recording,
    start_recording_on_pipeline,
    teardown_recording_branch,
)
from apps.streaming.gstreamer_streamer import (
    StreamingBranch,
    build_streaming_branch,
    finalize_hls_stream,
    start_streaming_on_pipeline,
    teardown_streaming_branch,
)
from apps.streaming.models import DestinationType
from apps.sessions.models import LayoutType
from core import events
from core.webhooks import emit_event

logger = logging.getLogger(__name__)

Gst.init(None)


@dataclass(frozen=True)
class MediasoupTransportTuple:
    """PlainTransport tuple returned by mediasoup (RTCP feedback target)."""

    ip: str
    port: int
    rtcp_port: int | None = None


@dataclass
class IngestChainResult:
    elements: list[Gst.Element]
    output: Gst.Element
    media_elements: list[Gst.Element] = field(default_factory=list)
    rtcp_links: list[tuple[Gst.Element, Gst.Element]] = field(default_factory=list)
    rtp_probe_pad: Gst.Pad | None = None
    rtcp_probe_pad: Gst.Pad | None = None
    signal_handlers: list[tuple[Gst.Element, int]] = field(default_factory=list)


@dataclass
class ParticipantBranch:
    participant_peer_id: str
    compositor_sink_pad: Gst.Pad
    mixer_sink_pad: Gst.Pad | None
    elements: list[Gst.Element] = field(default_factory=list)
    stats: IngestStats = field(default_factory=IngestStats)
    signal_handlers: list[tuple[Gst.Element, int]] = field(default_factory=list)
    source_url: str | None = None
    video_scale: Gst.Element | None = None


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
        self._bus_stop = threading.Event()
        self._bus_thread: threading.Thread | None = None

    def _start_bus_logger(self, pipeline: Gst.Pipeline) -> None:
        self._stop_bus_logger()
        self._bus_stop.clear()

        def _run() -> None:
            bus = pipeline.get_bus()
            while not self._bus_stop.is_set():
                message = bus.timed_pop_filtered(
                    200 * Gst.MSECOND,
                    Gst.MessageType.ERROR | Gst.MessageType.WARNING | Gst.MessageType.EOS,
                )
                if message is None:
                    continue
                src_name = message.src.get_name() if message.src else '?'
                if message.type == Gst.MessageType.ERROR:
                    err, debug = message.parse_error()
                    combined = f'{err} {debug or ""}'.lower()
                    # Expected when recording/streaming branches are torn down.
                    if 'not-linked' in combined:
                        logger.info(
                            'GStreamer not-linked during teardown session=%s src=%s: %s',
                            self.session_id,
                            src_name,
                            err,
                        )
                    else:
                        logger.error(
                            'GStreamer ERROR session=%s src=%s: %s (%s)',
                            self.session_id,
                            src_name,
                            err,
                            debug,
                        )
                elif message.type == Gst.MessageType.WARNING:
                    err, debug = message.parse_warning()
                    logger.warning(
                        'GStreamer WARNING session=%s src=%s: %s (%s)',
                        self.session_id,
                        src_name,
                        err,
                        debug,
                    )
                elif message.type == Gst.MessageType.EOS:
                    logger.info(
                        'GStreamer EOS session=%s src=%s',
                        self.session_id,
                        src_name,
                    )

        self._bus_thread = threading.Thread(
            target=_run,
            name=f'gst-bus-{self.session_id[:8]}',
            daemon=True,
        )
        self._bus_thread.start()

    def _stop_bus_logger(self) -> None:
        self._bus_stop.set()
        if self._bus_thread is not None:
            self._bus_thread.join(timeout=2)
            self._bus_thread = None

    def set_stream_failure_handler(self, handler: Callable[[str], None] | None) -> None:
        self._on_stream_permanent_failure = handler

    def start(self) -> None:
        with self._lock:
            if self._pipeline is not None:
                return

            pipeline = Gst.Pipeline.new(f'compositor-{self.session_id}')
            # force-live is construct-only on GstAggregator subclasses.
            compositor = Gst.ElementFactory.find('compositor').create_with_properties(
                ['name', 'force-live'],
                ['mix', True],
            )
            capsfilter = Gst.ElementFactory.make('capsfilter', 'out_caps')
            convert = Gst.ElementFactory.make('videoconvert', 'out_convert')
            video_tee = Gst.ElementFactory.make('tee', 'video_tee')
            video_queue = Gst.ElementFactory.make('queue', 'video_monitor_queue')
            sink = Gst.ElementFactory.make('fakesink', 'video_out')
            audiomixer = Gst.ElementFactory.find('audiomixer').create_with_properties(
                ['name', 'force-live'],
                ['amix', True],
            )
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

            compositor.set_property('background', 1)  # black — output frames even before first input
            compositor.set_property('start-time-selection', 0)  # first
            compositor.set_property('latency', 40 * Gst.MSECOND)
            # Guest pads often link before first decoded frame (or host audio never arrives).
            # Without this, aggregator waits forever and composited_frames stalls at ~1.
            compositor.set_property('ignore-inactive-pads', True)

            capsfilter.set_property(
                'caps',
                Gst.Caps.from_string(
                    f'video/x-raw,width={self.width},height={self.height}'
                ),
            )
            audio_capsfilter.set_property(
                'caps',
                Gst.Caps.from_string('audio/x-raw,rate=48000,channels=2'),
            )
            sink.set_property('sync', False)
            audio_sink.set_property('sync', False)
            audiomixer.set_property('latency', 40 * Gst.MSECOND)
            audiomixer.set_property('ignore-inactive-pads', True)
            video_tee.set_property('allow-not-linked', True)
            audio_tee.set_property('allow-not-linked', True)
            video_queue.set_property('leaky', 2)
            video_queue.set_property('max-size-time', 2 * Gst.SECOND)
            audio_queue.set_property('leaky', 2)
            audio_queue.set_property('max-size-time', 2 * Gst.SECOND)

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
            self._start_bus_logger(pipeline)

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

            self._stop_bus_logger()

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
                fps=self.fps,
            )

            start_recording_on_pipeline(
                self._pipeline,
                branch,
                video_tee=self._video_tee,
                audio_tee=self._audio_tee,
            )

            self._recording = branch
            logger.info(
                'Recording started for session %s -> %s (composited_frames=%s)',
                self.session_id,
                file_path,
                self._composited_frames,
            )

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

            # Create → add → link → tee attach → sync (same order as recording).
            # Linking before pipeline.add left RTMP with no media on Twitch.
            branch = build_streaming_branch(
                destination_type=destination_type,
                destination_url=destination_url,
                output_dir=output_dir,
                video_bitrate=settings.STREAMING_VIDEO_BITRATE,
                audio_bitrate=settings.STREAMING_AUDIO_BITRATE,
            )
            start_streaming_on_pipeline(
                self._pipeline,
                branch,
                video_tee=self._video_tee,
                audio_tee=self._audio_tee,
            )

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
        audio_rtcp_port: int,
        video_rtcp_port: int,
        audio_payload_type: int,
        video_payload_type: int,
        audio_mediasoup_transport: MediasoupTransportTuple,
        video_mediasoup_transport: MediasoupTransportTuple,
        rtcp_mux: bool = False,
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
                audio_rtcp_port=audio_rtcp_port,
                video_rtcp_port=video_rtcp_port,
                audio_payload_type=audio_payload_type,
                video_payload_type=video_payload_type,
                audio_mediasoup_transport=audio_mediasoup_transport,
                video_mediasoup_transport=video_mediasoup_transport,
                rtcp_mux=rtcp_mux,
            )
            self._participants[participant_peer_id] = branch

            if self._host_peer_id is None and not participant_peer_id.startswith('rtmp-'):
                self._host_peer_id = participant_peer_id

            self._apply_layout_unlocked()
            return branch.stats

    def remove_participant(self, participant_peer_id: str) -> None:
        with self._lock:
            self._remove_participant_unlocked(participant_peer_id)

            if self._host_peer_id == participant_peer_id:
                self._host_peer_id = next(
                    (
                        pid
                        for pid in self._participants
                        if not pid.startswith('rtmp-')
                    ),
                    next(iter(self._participants), None),
                )

            self._apply_layout_unlocked()

    def add_rtmp_source(self, source_id: str, *, url: str, display_name: str = '') -> IngestStats:
        with self._lock:
            if self._pipeline is None:
                raise RuntimeError('Compositor pipeline is not started')

            if source_id in self._participants:
                return self._participants[source_id].stats

            branch = self._build_rtmp_source_branch(
                source_id=source_id,
                url=url,
                display_name=display_name,
            )
            self._participants[source_id] = branch
            self._apply_layout_unlocked()
            logger.info(
                'RTMP source added for session %s (source=%s url=%s)',
                self.session_id,
                source_id,
                url,
            )
            return branch.stats

    def remove_rtmp_source(self, source_id: str) -> None:
        with self._lock:
            self._remove_participant_unlocked(source_id)
            self._apply_layout_unlocked()

    def get_rtmp_source_stats(self, source_id: str) -> IngestStats | None:
        branch = self._participants.get(source_id)
        if branch is None or not source_id.startswith('rtmp-'):
            return None
        return branch.stats

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
            start_streaming_on_pipeline(
                self._pipeline,
                new_branch,
                video_tee=self._video_tee,
                audio_tee=self._audio_tee,
            )
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
            assert self._video_tee is not None
            assert self._audio_tee is not None
            finalize_recording(
                branch,
                self._pipeline,
                timeout_sec=settings.RECORDING_EOS_TIMEOUT_SEC,
                composited_frames=self._composited_frames,
                participant_ingest=self._participant_ingest_summary_unlocked(),
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

    def _participant_ingest_summary_unlocked(self) -> str:
        if not self._participants:
            return 'no participants attached'

        parts = []
        for peer_id, branch in self._participants.items():
            stats = branch.stats
            parts.append(
                f'{peer_id}: rtp(v={stats.rtp_video_packets},a={stats.rtp_audio_packets}) '
                f'rtcp(v={stats.rtcp_video_packets},a={stats.rtcp_audio_packets}) '
                f'decoded(v={stats.video_buffers},a={stats.audio_buffers})'
            )
        return '; '.join(parts)

    def _build_participant_branch(
        self,
        *,
        participant_peer_id: str,
        audio_port: int,
        video_port: int,
        audio_rtcp_port: int,
        video_rtcp_port: int,
        audio_payload_type: int,
        video_payload_type: int,
        audio_mediasoup_transport: MediasoupTransportTuple,
        video_mediasoup_transport: MediasoupTransportTuple,
        rtcp_mux: bool,
    ) -> ParticipantBranch:
        assert self._pipeline is not None
        assert self._compositor is not None
        assert self._audiomixer is not None

        video_chain = self._build_video_ingest_chain(
            participant_peer_id=participant_peer_id,
            rtp_port=video_port,
            rtcp_port=video_rtcp_port,
            payload_type=video_payload_type,
            mediasoup_transport=video_mediasoup_transport,
            rtcp_mux=rtcp_mux,
        )
        audio_chain = self._build_audio_ingest_chain(
            participant_peer_id=participant_peer_id,
            rtp_port=audio_port,
            rtcp_port=audio_rtcp_port,
            payload_type=audio_payload_type,
            mediasoup_transport=audio_mediasoup_transport,
            rtcp_mux=rtcp_mux,
        )
        all_elements = video_chain.elements + audio_chain.elements

        for element in all_elements:
            self._pipeline.add(element)

        # Link only after elements are in the pipeline.
        self._link_sequential(
            video_chain.media_elements,
            label=f'video-{participant_peer_id}',
        )
        self._link_sequential(
            audio_chain.media_elements,
            label=f'audio-{participant_peer_id}',
        )
        for upstream, downstream in video_chain.rtcp_links + audio_chain.rtcp_links:
            if not upstream.link(downstream):
                raise RuntimeError(
                    f'Failed to link RTCP drain for {participant_peer_id}'
                )

        compositor_sink_pad = self._compositor.get_request_pad('sink_%u')
        if compositor_sink_pad is None:
            raise RuntimeError(f'Failed to request compositor sink pad for {participant_peer_id}')

        mixer_pad = self._audiomixer.get_request_pad('sink_%u')
        if mixer_pad is None:
            raise RuntimeError(f'Failed to request audiomixer sink pad for {participant_peer_id}')

        video_src_pad = video_chain.output.get_static_pad('src')
        if video_src_pad is None or video_src_pad.link(compositor_sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError(
                f'Failed to link video branch to compositor for {participant_peer_id}'
            )

        audio_src_pad = audio_chain.output.get_static_pad('src')
        if audio_src_pad is None or audio_src_pad.link(mixer_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError(
                f'Failed to link audio branch to audiomixer for {participant_peer_id}'
            )

        video_scale = next(
            (
                element
                for element in video_chain.media_elements
                if element.get_factory() is not None
                and element.get_factory().get_name() == 'videoscale'
            ),
            None,
        )
        branch = ParticipantBranch(
            participant_peer_id=participant_peer_id,
            compositor_sink_pad=compositor_sink_pad,
            mixer_sink_pad=mixer_pad,
            elements=all_elements,
            stats=IngestStats(),
            signal_handlers=video_chain.signal_handlers + audio_chain.signal_handlers,
            video_scale=video_scale,
        )

        if video_chain.rtp_probe_pad is not None:
            video_chain.rtp_probe_pad.add_probe(
                Gst.PadProbeType.BUFFER,
                self._make_rtp_video_probe(branch),
                None,
            )
        if audio_chain.rtp_probe_pad is not None:
            audio_chain.rtp_probe_pad.add_probe(
                Gst.PadProbeType.BUFFER,
                self._make_rtp_audio_probe(branch),
                None,
            )
        if video_chain.rtcp_probe_pad is not None:
            video_chain.rtcp_probe_pad.add_probe(
                Gst.PadProbeType.BUFFER,
                self._make_rtcp_video_probe(branch),
                None,
            )
        if audio_chain.rtcp_probe_pad is not None:
            audio_chain.rtcp_probe_pad.add_probe(
                Gst.PadProbeType.BUFFER,
                self._make_rtcp_audio_probe(branch),
                None,
            )

        # Stage probes: jb → depay → decoder (pinpoint where video stalls).
        video_jb_src = video_chain.media_elements[1].get_static_pad('src')
        audio_jb_src = audio_chain.media_elements[1].get_static_pad('src')
        video_depay_src = video_chain.media_elements[2].get_static_pad('src')
        video_dec_src = video_chain.media_elements[3].get_static_pad('src')
        audio_dec_src = audio_chain.media_elements[3].get_static_pad('src')
        if video_jb_src is not None:
            video_jb_src.add_probe(
                Gst.PadProbeType.BUFFER,
                self._make_count_probe(branch, 'video_jb'),
                None,
            )
        if audio_jb_src is not None:
            audio_jb_src.add_probe(
                Gst.PadProbeType.BUFFER,
                self._make_count_probe(branch, 'audio_jb'),
                None,
            )
        if video_depay_src is not None:
            video_depay_src.add_probe(
                Gst.PadProbeType.BUFFER,
                self._make_count_probe(branch, 'video_depay'),
                None,
            )
        if video_dec_src is not None:
            video_dec_src.add_probe(
                Gst.PadProbeType.BUFFER,
                self._make_count_probe(branch, 'video_dec'),
                None,
            )
        if audio_dec_src is not None:
            audio_dec_src.add_probe(
                Gst.PadProbeType.BUFFER,
                self._make_count_probe(branch, 'audio_dec'),
                None,
            )

        video_src_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            self._make_running_time_offset_probe(),
            None,
        )
        audio_src_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            self._make_running_time_offset_probe(),
            None,
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

        # Downstream → upstream so udpsrc never goes PLAYING against a NULL peer.
        for element in reversed(all_elements):
            element.sync_state_with_parent()

        return branch

    def _build_rtcp_drain_chain(
        self,
        *,
        participant_peer_id: str,
        port: int,
        label: str,
    ) -> tuple[list[Gst.Element], tuple[Gst.Element, Gst.Element]]:
        """Create RTCP udpsrc → fakesink (link after pipeline.add)."""
        src = Gst.ElementFactory.make('udpsrc', f'{label}_rtcp_{participant_peer_id}')
        sink = Gst.ElementFactory.make('fakesink', f'{label}_rtcp_sink_{participant_peer_id}')
        if not src or not sink:
            raise RuntimeError(
                f'Failed to create RTCP drain elements for {participant_peer_id} ({label})'
            )

        src.set_property('port', port)
        src.set_property('address', '0.0.0.0')
        sink.set_property('sync', False)
        sink.set_property('async', False)

        return [src, sink], (src, sink)

    def _build_video_ingest_chain(
        self,
        *,
        participant_peer_id: str,
        rtp_port: int,
        rtcp_port: int,
        payload_type: int,
        mediasoup_transport: MediasoupTransportTuple,
        rtcp_mux: bool,
    ) -> IngestChainResult:
        scale = Gst.ElementFactory.make('videoscale', f'video_scale_{participant_peer_id}')
        queue = Gst.ElementFactory.make('queue', f'video_queue_{participant_peer_id}')
        if scale is None or queue is None:
            raise RuntimeError(f'Failed to create video scale/queue for {participant_peer_id}')
        scale.set_property('add-borders', True)
        queue.set_property('leaky', 2)
        queue.set_property('max-size-time', 2 * Gst.SECOND)

        return self._build_rtp_ingest_chain(
            participant_peer_id=participant_peer_id,
            label='video',
            rtp_port=rtp_port,
            rtcp_port=rtcp_port,
            mediasoup_transport=mediasoup_transport,
            rtcp_mux=rtcp_mux,
            rtp_caps=(
                f'application/x-rtp,media=video,clock-rate=90000,encoding-name=VP8,'
                f'payload={payload_type}'
            ),
            depay='rtpvp8depay',
            decoder='vp8dec',
            tail_elements=[
                Gst.ElementFactory.make('videoconvert', f'video_convert_{participant_peer_id}'),
                scale,
                queue,
            ],
        )

    def _build_audio_ingest_chain(
        self,
        *,
        participant_peer_id: str,
        rtp_port: int,
        rtcp_port: int,
        payload_type: int,
        mediasoup_transport: MediasoupTransportTuple,
        rtcp_mux: bool,
    ) -> IngestChainResult:
        audio_queue = Gst.ElementFactory.make('queue', f'audio_queue_{participant_peer_id}')
        if audio_queue is not None:
            audio_queue.set_property('leaky', 2)
            audio_queue.set_property('max-size-time', 2 * Gst.SECOND)
        tail_elements = [
            Gst.ElementFactory.make('audioconvert', f'audio_convert_{participant_peer_id}'),
            Gst.ElementFactory.make('audioresample', f'audio_resample_{participant_peer_id}'),
            audio_queue,
        ]
        return self._build_rtp_ingest_chain(
            participant_peer_id=participant_peer_id,
            label='audio',
            rtp_port=rtp_port,
            rtcp_port=rtcp_port,
            mediasoup_transport=mediasoup_transport,
            rtcp_mux=rtcp_mux,
            rtp_caps=(
                f'application/x-rtp,media=audio,clock-rate=48000,encoding-name=OPUS,'
                f'payload={payload_type}'
            ),
            depay='rtpopusdepay',
            decoder='opusdec',
            tail_elements=tail_elements,
        )

    def _build_rtp_ingest_chain(
        self,
        *,
        participant_peer_id: str,
        label: str,
        rtp_port: int,
        rtcp_port: int,
        mediasoup_transport: MediasoupTransportTuple,
        rtcp_mux: bool,
        rtp_caps: str,
        depay: str,
        decoder: str,
        tail_elements: list[Gst.Element | None],
    ) -> IngestChainResult:
        # Static jitterbuffer path — avoids rtpbin dynamic-pad + Python GIL stalls.
        # Elements are created here; linking happens after pipeline.add().
        _ = mediasoup_transport
        rtp_src = Gst.ElementFactory.make('udpsrc', f'{label}_rtp_src_{participant_peer_id}')
        jitter = Gst.ElementFactory.make('rtpjitterbuffer', f'{label}_jitter_{participant_peer_id}')
        depay_el = Gst.ElementFactory.make(depay, f'{label}_depay_{participant_peer_id}')
        decoder_el = Gst.ElementFactory.make(decoder, f'{label}_dec_{participant_peer_id}')

        if not all([rtp_src, jitter, depay_el, decoder_el, *tail_elements]):
            raise RuntimeError(
                f'Failed to create {label} ingest elements for {participant_peer_id}'
            )

        rtp_src.set_property('port', rtp_port)
        rtp_src.set_property('address', '0.0.0.0')
        rtp_src.set_property('caps', Gst.Caps.from_string(rtp_caps))
        # Arrival-time stamps help downstream aggregators; mediasoup RTP clocks
        # are not synchronized to this pipeline, so slave jitterbuffer mode
        # holds forever when the branch is hot-added into an already-PLAYING
        # compositor (percent stays 0, decoded stays 0).
        rtp_src.set_property('do-timestamp', True)
        jitter.set_property('mode', 0)  # none — reorder only, no clock skew
        # VP8 keyframes are large/multi-packet. drop-on-latency with a short
        # window discards late keyframe fragments → rtpvp8depay never emits →
        # decoded(v=0) forever while video_jb still climbs. Audio Opus is
        # typically 1 packet/frame so it survived the aggressive setting.
        if label == 'video':
            jitter.set_property('latency', 200)
            jitter.set_property('drop-on-latency', False)
        else:
            jitter.set_property('latency', 100)
            jitter.set_property('drop-on-latency', True)

        logger.info(
            'Built %s RTP ingest for peer %s (rtp_port=%s rtcp_port=%s caps=%s)',
            label,
            participant_peer_id,
            rtp_port,
            rtcp_port,
            rtp_caps,
        )

        media_elements: list[Gst.Element] = [
            rtp_src,
            jitter,
            depay_el,
            decoder_el,
            *tail_elements,  # type: ignore[list-item]
        ]
        elements: list[Gst.Element] = list(media_elements)
        rtp_probe_pad = rtp_src.get_static_pad('src')
        rtcp_probe_pad: Gst.Pad | None = None
        rtcp_links: list[tuple[Gst.Element, Gst.Element]] = []
        signal_handlers: list[tuple[Gst.Element, int]] = []

        if not rtcp_mux:
            rtcp_elements, rtcp_pair = self._build_rtcp_drain_chain(
                participant_peer_id=participant_peer_id,
                port=rtcp_port,
                label=label,
            )
            elements.extend(rtcp_elements)
            rtcp_links.append(rtcp_pair)
            rtcp_probe_pad = rtcp_elements[0].get_static_pad('src')

        return IngestChainResult(
            elements=elements,
            output=tail_elements[-1],  # type: ignore[arg-type]
            media_elements=media_elements,
            rtcp_links=rtcp_links,
            rtp_probe_pad=rtp_probe_pad,
            rtcp_probe_pad=rtcp_probe_pad,
            signal_handlers=signal_handlers,
        )

    def _remove_participant_unlocked(self, participant_peer_id: str) -> None:
        branch = self._participants.pop(participant_peer_id, None)
        if branch is None or self._pipeline is None:
            return

        compositor_peer = branch.compositor_sink_pad.get_peer()
        if compositor_peer is not None:
            compositor_peer.unlink(branch.compositor_sink_pad)

        mixer_peer = None
        if branch.mixer_sink_pad is not None:
            mixer_peer = branch.mixer_sink_pad.get_peer()
            if mixer_peer is not None:
                mixer_peer.unlink(branch.mixer_sink_pad)

        for element in branch.elements:
            element.set_state(Gst.State.NULL)
            self._pipeline.remove(element)

        for element, handler_id in branch.signal_handlers:
            element.disconnect(handler_id)

        if self._compositor is not None:
            self._compositor.release_request_pad(branch.compositor_sink_pad)

        if self._audiomixer is not None and branch.mixer_sink_pad is not None:
            self._audiomixer.release_request_pad(branch.mixer_sink_pad)

    def _build_rtmp_source_branch(
        self,
        *,
        source_id: str,
        url: str,
        display_name: str,
    ) -> ParticipantBranch:
        assert self._pipeline is not None
        assert self._compositor is not None
        assert self._audiomixer is not None

        _ = display_name
        src = Gst.ElementFactory.make('uridecodebin', f'rtmp_src_{source_id}')
        video_convert = Gst.ElementFactory.make('videoconvert', f'rtmp_v_convert_{source_id}')
        video_scale = Gst.ElementFactory.make('videoscale', f'rtmp_v_scale_{source_id}')
        audio_convert = Gst.ElementFactory.make('audioconvert', f'rtmp_a_convert_{source_id}')
        audio_resample = Gst.ElementFactory.make('audioresample', f'rtmp_a_resample_{source_id}')
        audio_queue = Gst.ElementFactory.make('queue', f'rtmp_a_queue_{source_id}')

        if not all([src, video_convert, video_scale, audio_convert, audio_resample, audio_queue]):
            raise RuntimeError(f'Failed to create RTMP ingest elements for {source_id}')

        src.set_property('uri', url)
        video_scale.set_property('add-borders', True)

        elements = [
            src,
            video_convert,
            video_scale,
            audio_convert,
            audio_resample,
            audio_queue,
        ]
        self._link_sequential(
            [video_convert, video_scale],
            label=f'rtmp-video-{source_id}',
        )
        self._link_sequential(
            [audio_convert, audio_resample, audio_queue],
            label=f'rtmp-audio-{source_id}',
        )

        for element in elements:
            self._pipeline.add(element)

        compositor_sink_pad = self._compositor.get_request_pad('sink_%u')
        if compositor_sink_pad is None:
            raise RuntimeError(f'Failed to request compositor sink pad for {source_id}')

        video_src_pad = video_scale.get_static_pad('src')
        if video_src_pad is None or video_src_pad.link(compositor_sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError(f'Failed to link RTMP video branch to compositor for {source_id}')

        branch = ParticipantBranch(
            participant_peer_id=source_id,
            compositor_sink_pad=compositor_sink_pad,
            mixer_sink_pad=None,
            elements=elements,
            stats=IngestStats(),
            source_url=url,
            video_scale=video_scale,
        )
        video_src_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            self._make_video_probe(branch),
            None,
        )
        link_state = {'audio': False}

        def on_pad_added(_element: Gst.Element, pad: Gst.Pad, _user_data) -> None:
            caps = pad.get_current_caps()
            if caps is None:
                caps = pad.query_caps(None)
            if caps is None:
                return

            structure = caps.get_structure(0)
            if structure is None:
                return

            media_name = structure.get_name()
            if media_name.startswith('video/'):
                sink_pad = video_convert.get_static_pad('sink')
                if sink_pad is None or sink_pad.is_linked():
                    return
                if pad.link(sink_pad) != Gst.PadLinkReturn.OK:
                    raise RuntimeError(f'Failed to link RTMP video pad for {source_id}')
            elif media_name.startswith('audio/') and not link_state['audio']:
                mixer_pad = self._audiomixer.get_request_pad('sink_%u')
                if mixer_pad is None:
                    raise RuntimeError(f'Failed to request audiomixer sink pad for {source_id}')

                sink_pad = audio_convert.get_static_pad('sink')
                if sink_pad is None or sink_pad.is_linked():
                    return
                if pad.link(sink_pad) != Gst.PadLinkReturn.OK:
                    raise RuntimeError(f'Failed to link RTMP audio pad for {source_id}')

                audio_src_pad = audio_queue.get_static_pad('src')
                if audio_src_pad is None or audio_src_pad.link(mixer_pad) != Gst.PadLinkReturn.OK:
                    raise RuntimeError(f'Failed to link RTMP audio branch to audiomixer for {source_id}')

                branch.mixer_sink_pad = mixer_pad
                link_state['audio'] = True
                audio_src_pad.add_probe(
                    Gst.PadProbeType.BUFFER,
                    self._make_audio_probe(branch),
                    None,
                )

        handler_id = src.connect('pad-added', on_pad_added, None)
        branch.signal_handlers.append((src, handler_id))

        for element in elements:
            element.sync_state_with_parent()

        return branch

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
                # Hide sources not included in this layout (e.g. FULLSCREEN guests).
                self._hide_pad(branch.compositor_sink_pad)
                continue
            self._apply_tile_to_pad(branch, tile)

    @staticmethod
    def _hide_pad(pad: Gst.Pad) -> None:
        pad.set_property('xpos', 0)
        pad.set_property('ypos', 0)
        pad.set_property('width', 1)
        pad.set_property('height', 1)
        pad.set_property('zorder', 0)
        pad.set_property('alpha', 0.0)

    @staticmethod
    def _apply_tile_to_pad(branch: ParticipantBranch, tile: TileConfig) -> None:
        pad = branch.compositor_sink_pad
        pad.set_property('xpos', tile.x)
        pad.set_property('ypos', tile.y)
        pad.set_property('width', tile.width)
        pad.set_property('height', tile.height)
        pad.set_property('zorder', tile.zorder)
        pad.set_property('alpha', 1.0)

        # Prefer compositor sizing-policy when available (GStreamer ≥ 1.20).
        # keep-aspect-ratio ≈ contain; none ≈ fill/stretch (cover approximation).
        if pad.find_property('sizing-policy') is not None:
            pad.set_property(
                'sizing-policy',
                'keep-aspect-ratio' if tile.scale_mode == ScaleMode.CONTAIN else 'none',
            )

        if branch.video_scale is not None:
            branch.video_scale.set_property(
                'add-borders',
                tile.scale_mode == ScaleMode.CONTAIN,
            )

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
    def _should_log_ingest_count(count: int) -> bool:
        """Log first packet, then 10/50/100, then every 100 thereafter."""
        return count in (1, 10, 50, 100) or (count > 100 and count % 100 == 0)

    @staticmethod
    def _make_count_probe(branch: ParticipantBranch, stage: str):
        """Log buffers at intermediate pads (jitterbuffer / decoder)."""

        def _probe(_pad: Gst.Pad, _info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            key = f'_stage_{stage}'
            count = getattr(branch.stats, key, 0) + 1
            setattr(branch.stats, key, count)
            if CompositorPipeline._should_log_ingest_count(count):
                logger.info(
                    'Ingest stage=%s packets=%s peer=%s',
                    stage,
                    count,
                    branch.participant_peer_id,
                )
            return Gst.PadProbeReturn.OK

        return _probe

    def _make_running_time_offset_probe(self):
        """
        Keep buffer PTS aligned to pipeline running time.

        Mediasoup RTP timestamps are an arbitrary offset from this pipeline's
        clock. A one-shot offset drifts; compositor/audiomixer then hold or
        drop buffers even while the decoder keeps producing frames.
        """
        state = {'logged': False}

        def _probe(pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            if self._pipeline is None:
                return Gst.PadProbeReturn.OK

            buffer = info.get_buffer()
            if buffer is None or buffer.pts == Gst.CLOCK_TIME_NONE:
                return Gst.PadProbeReturn.OK

            clock = self._pipeline.get_clock()
            if clock is None:
                return Gst.PadProbeReturn.OK

            running_time = clock.get_time() - self._pipeline.get_base_time()
            if running_time < 0:
                return Gst.PadProbeReturn.OK

            pad.set_offset(int(running_time) - int(buffer.pts))
            if not state['logged']:
                state['logged'] = True
                logger.info(
                    'Applied running-time pad offset=%s on %s',
                    pad.get_offset(),
                    pad.get_path_string(),
                )
            return Gst.PadProbeReturn.OK

        return _probe

    @staticmethod
    def _make_rtcp_video_probe(branch: ParticipantBranch):
        def _probe(_pad: Gst.Pad, _info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            branch.stats.rtcp_video_packets += 1
            count = branch.stats.rtcp_video_packets
            if CompositorPipeline._should_log_ingest_count(count):
                logger.info(
                    'Ingest RTCP video packets=%s peer=%s',
                    count,
                    branch.participant_peer_id,
                )
            return Gst.PadProbeReturn.OK

        return _probe

    @staticmethod
    def _make_rtcp_audio_probe(branch: ParticipantBranch):
        def _probe(_pad: Gst.Pad, _info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            branch.stats.rtcp_audio_packets += 1
            count = branch.stats.rtcp_audio_packets
            if CompositorPipeline._should_log_ingest_count(count):
                logger.info(
                    'Ingest RTCP audio packets=%s peer=%s',
                    count,
                    branch.participant_peer_id,
                )
            return Gst.PadProbeReturn.OK

        return _probe

    @staticmethod
    def _rtp_payload_type_from_buffer(info: Gst.PadProbeInfo) -> int | None:
        buffer = info.get_buffer()
        if buffer is None or buffer.get_size() < 2:
            return None
        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success:
            return None
        try:
            return map_info.data[1] & 0x7F
        finally:
            buffer.unmap(map_info)

    @staticmethod
    def _make_rtp_video_probe(branch: ParticipantBranch):
        logged_wire_pt = {'done': False}

        def _probe(_pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            branch.stats.rtp_video_packets += 1
            count = branch.stats.rtp_video_packets
            if not logged_wire_pt['done']:
                wire_pt = CompositorPipeline._rtp_payload_type_from_buffer(info)
                if wire_pt is not None:
                    logged_wire_pt['done'] = True
                    logger.info(
                        'First video RTP wire payload type=%s peer=%s',
                        wire_pt,
                        branch.participant_peer_id,
                    )
            if CompositorPipeline._should_log_ingest_count(count):
                logger.info(
                    'Ingest RTP video packets=%s decoded=%s peer=%s',
                    count,
                    branch.stats.video_buffers,
                    branch.participant_peer_id,
                )
            return Gst.PadProbeReturn.OK

        return _probe

    @staticmethod
    def _make_rtp_audio_probe(branch: ParticipantBranch):
        logged_wire_pt = {'done': False}

        def _probe(_pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            branch.stats.rtp_audio_packets += 1
            count = branch.stats.rtp_audio_packets
            if not logged_wire_pt['done']:
                wire_pt = CompositorPipeline._rtp_payload_type_from_buffer(info)
                if wire_pt is not None:
                    logged_wire_pt['done'] = True
                    logger.info(
                        'First audio RTP wire payload type=%s peer=%s',
                        wire_pt,
                        branch.participant_peer_id,
                    )
            if CompositorPipeline._should_log_ingest_count(count):
                logger.info(
                    'Ingest RTP audio packets=%s decoded=%s peer=%s',
                    count,
                    branch.stats.audio_buffers,
                    branch.participant_peer_id,
                )
            return Gst.PadProbeReturn.OK

        return _probe

    @staticmethod
    def _make_video_probe(branch: ParticipantBranch):
        def _probe(pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            branch.stats.video_buffers += 1
            count = branch.stats.video_buffers
            if CompositorPipeline._should_log_ingest_count(count):
                logger.info(
                    'Ingest decoded video frames=%s rtp=%s peer=%s',
                    count,
                    branch.stats.rtp_video_packets,
                    branch.participant_peer_id,
                )
            return Gst.PadProbeReturn.OK

        return _probe

    @staticmethod
    def _make_audio_probe(branch: ParticipantBranch):
        def _probe(pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            branch.stats.audio_buffers += 1
            count = branch.stats.audio_buffers
            if CompositorPipeline._should_log_ingest_count(count):
                logger.info(
                    'Ingest decoded audio buffers=%s rtp=%s peer=%s',
                    count,
                    branch.stats.rtp_audio_packets,
                    branch.participant_peer_id,
                )
            return Gst.PadProbeReturn.OK

        return _probe
