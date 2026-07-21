"""GStreamer RTP ingest branch for a single participant."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

logger = logging.getLogger(__name__)

Gst.init(None)


@dataclass
class IngestStats:
    audio_buffers: int = 0
    video_buffers: int = 0
    rtp_audio_packets: int = 0
    rtp_video_packets: int = 0
    rtcp_audio_packets: int = 0
    rtcp_video_packets: int = 0


@dataclass
class IngestBranch:
    """
    Receives Opus/VP8 RTP from mediasoup via udpsrc and decodes to fakesink.

    Phase 3 proof: two mini-pipelines (audio + video) per participant.
    Phase 4 will feed decoded frames into the compositor element.
    """

    participant_peer_id: str
    audio_port: int
    video_port: int
    audio_payload_type: int
    video_payload_type: int
    _audio_pipeline: Gst.Pipeline | None = field(default=None, init=False, repr=False)
    _video_pipeline: Gst.Pipeline | None = field(default=None, init=False, repr=False)
    _stats: IngestStats = field(default_factory=IngestStats, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def start(self) -> None:
        with self._lock:
            if self._audio_pipeline is not None:
                return

            self._audio_pipeline = self._build_audio_pipeline(
                pipeline_name=f'ingest-audio-{self.participant_peer_id}',
                port=self.audio_port,
                caps=(
                    f'application/x-rtp,media=audio,clock-rate=48000,'
                    f'encoding-name=OPUS,payload={self.audio_payload_type}'
                ),
                depay='rtpopusdepay',
                decoder='opusdec',
                convert='audioconvert',
                on_buffer=self._on_audio_buffer,
            )
            self._video_pipeline = self._build_video_pipeline()

            for pipeline in (self._audio_pipeline, self._video_pipeline):
                ret = pipeline.set_state(Gst.State.PLAYING)
                if ret == Gst.StateChangeReturn.FAILURE:
                    raise RuntimeError(
                        f'Failed to start ingest pipeline for {self.participant_peer_id}'
                    )

            logger.info(
                'Ingest branch started for %s (audio:%s video:%s)',
                self.participant_peer_id,
                self.audio_port,
                self.video_port,
            )

    def stop(self) -> None:
        with self._lock:
            for pipeline in (self._audio_pipeline, self._video_pipeline):
                if pipeline is not None:
                    pipeline.set_state(Gst.State.NULL)

            self._audio_pipeline = None
            self._video_pipeline = None

    @property
    def stats(self) -> IngestStats:
        return self._stats

    def _build_video_pipeline(self) -> Gst.Pipeline:
        return self._build_audio_pipeline(
            pipeline_name=f'ingest-video-{self.participant_peer_id}',
            port=self.video_port,
            caps=(
                f'application/x-rtp,media=video,clock-rate=90000,'
                f'encoding-name=VP8,payload={self.video_payload_type}'
            ),
            depay='rtpvp8depay',
            decoder='vp8dec',
            convert='videoconvert',
            on_buffer=self._on_video_buffer,
        )

    def _build_audio_pipeline(
        self,
        *,
        pipeline_name: str,
        port: int,
        caps: str,
        depay: str,
        decoder: str,
        convert: str,
        on_buffer,
    ) -> Gst.Pipeline:
        pipeline = Gst.Pipeline.new(pipeline_name)
        src = Gst.ElementFactory.make('udpsrc', None)
        jitter = Gst.ElementFactory.make('rtpjitterbuffer', None)
        depay_el = Gst.ElementFactory.make(depay, None)
        decoder_el = Gst.ElementFactory.make(decoder, None)
        convert_el = Gst.ElementFactory.make(convert, None)
        sink = Gst.ElementFactory.make('fakesink', None)

        if not all([src, jitter, depay_el, decoder_el, convert_el, sink]):
            raise RuntimeError(f'Failed to create GStreamer ingest elements for {pipeline_name}')

        src.set_property('port', port)
        src.set_property('caps', Gst.Caps.from_string(caps))
        src.set_property('do-timestamp', True)
        jitter.set_property('latency', 100)
        jitter.set_property('mode', 0)
        jitter.set_property('drop-on-latency', True)
        sink.set_property('sync', False)

        for element in (src, jitter, depay_el, decoder_el, convert_el, sink):
            pipeline.add(element)

        if not src.link(jitter):
            raise RuntimeError(f'Failed to link udpsrc -> rtpjitterbuffer ({pipeline_name})')
        if not jitter.link(depay_el):
            raise RuntimeError(f'Failed to link jitter -> depay ({pipeline_name})')
        if not depay_el.link(decoder_el):
            raise RuntimeError(f'Failed to link depay -> decoder ({pipeline_name})')
        if not decoder_el.link(convert_el):
            raise RuntimeError(f'Failed to link decoder -> convert ({pipeline_name})')
        if not convert_el.link(sink):
            raise RuntimeError(f'Failed to link convert -> sink ({pipeline_name})')

        pad = sink.get_static_pad('sink')
        pad.add_probe(Gst.PadProbeType.BUFFER, on_buffer, None)

        return pipeline

    def _on_audio_buffer(self, pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
        self._stats.audio_buffers += 1
        return Gst.PadProbeReturn.OK

    def _on_video_buffer(self, pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
        self._stats.video_buffers += 1
        return Gst.PadProbeReturn.OK
