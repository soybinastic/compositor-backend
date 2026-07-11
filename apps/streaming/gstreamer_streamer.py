"""GStreamer streaming branches: RTMP and HLS egress from compositor output."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

from apps.streaming.models import DestinationType

logger = logging.getLogger(__name__)


@dataclass
class StreamingBranch:
    destination_type: str
    destination_url: str
    output_path: Path | None
    elements: list[Gst.Element] = field(default_factory=list)
    mux: Gst.Element | None = None
    sink: Gst.Element | None = None
    video_tee_pad: Gst.Pad | None = None
    audio_tee_pad: Gst.Pad | None = None


def _make_encoder(factory_names: tuple[str, ...], name: str) -> Gst.Element:
    for factory_name in factory_names:
        element = Gst.ElementFactory.make(factory_name, name)
        if element is not None:
            return element
    raise RuntimeError(f'No GStreamer encoder available from {factory_names}')


def _make_sink(factory_names: tuple[str, ...], name: str) -> Gst.Element:
    for factory_name in factory_names:
        element = Gst.ElementFactory.make(factory_name, name)
        if element is not None:
            return element
    raise RuntimeError(f'No GStreamer sink available from {factory_names}')


def _configure_video_encoder(venc: Gst.Element, *, video_bitrate: int) -> None:
    factory_name = venc.get_factory().get_name()
    if factory_name == 'x264enc':
        venc.set_property('speed-preset', 'ultrafast')
        venc.set_property('tune', 'zerolatency')
        venc.set_property('key-int-max', 60)
        venc.set_property('bitrate', max(video_bitrate // 1000, 500))
    elif factory_name == 'openh264enc':
        venc.set_property('bitrate', video_bitrate)


def _configure_audio_encoder(aenc: Gst.Element, *, audio_bitrate: int) -> None:
    if aenc.get_factory().get_name() == 'avenc_aac':
        aenc.set_property('bitrate', audio_bitrate)


def build_rtmp_streaming_branch(
    *,
    destination_url: str,
    video_bitrate: int,
    audio_bitrate: int,
) -> StreamingBranch:
    """Build an FLV/RTMP live streaming subgraph."""
    v_queue = Gst.ElementFactory.make('queue', 'stream_v_queue')
    venc = _make_encoder(('x264enc', 'openh264enc'), 'stream_venc')
    h264parse = Gst.ElementFactory.make('h264parse', 'stream_h264parse')
    a_queue = Gst.ElementFactory.make('queue', 'stream_a_queue')
    aenc = _make_encoder(('avenc_aac', 'voaacenc', 'fdkaacenc'), 'stream_aenc')
    flvmux = Gst.ElementFactory.make('flvmux', 'stream_flvmux')
    sink = _make_sink(('rtmpsink', 'rtmp2sink'), 'stream_rtmp_sink')

    if not all([v_queue, h264parse, a_queue, flvmux]):
        raise RuntimeError('Failed to create RTMP streaming elements')

    _configure_video_encoder(venc, video_bitrate=video_bitrate)
    _configure_audio_encoder(aenc, audio_bitrate=audio_bitrate)

    flvmux.set_property('streamable', True)
    sink.set_property('location', destination_url)
    if sink.get_factory().get_name() == 'rtmpsink':
        sink.set_property('sync', False)
        sink.set_property('async', False)

    elements = [v_queue, venc, h264parse, a_queue, aenc, flvmux, sink]

    if not v_queue.link(venc):
        raise RuntimeError('Failed to link RTMP video queue -> encoder')
    if not venc.link(h264parse):
        raise RuntimeError('Failed to link RTMP video encoder -> h264parse')

    mux_video_pad = flvmux.get_request_pad('video')
    mux_audio_pad = flvmux.get_request_pad('audio')
    if mux_video_pad is None or mux_audio_pad is None:
        raise RuntimeError('Failed to request flvmux pads')

    if h264parse.get_static_pad('src').link(mux_video_pad) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link h264parse -> flvmux video pad')

    if not a_queue.link(aenc):
        raise RuntimeError('Failed to link RTMP audio queue -> encoder')

    if aenc.get_static_pad('src').link(mux_audio_pad) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link audio encoder -> flvmux audio pad')

    if not flvmux.link(sink):
        raise RuntimeError('Failed to link flvmux -> rtmpsink')

    return StreamingBranch(
        destination_type=DestinationType.RTMP,
        destination_url=destination_url,
        output_path=None,
        elements=elements,
        mux=flvmux,
        sink=sink,
    )


def build_hls_streaming_branch(
    *,
    output_dir: Path,
    video_bitrate: int,
    audio_bitrate: int,
) -> StreamingBranch:
    """Build a local HLS output subgraph (stub destination for dev/preview)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    segment_pattern = output_dir / 'segment_%05d.ts'
    playlist_path = output_dir / 'playlist.m3u8'

    v_queue = Gst.ElementFactory.make('queue', 'stream_v_queue')
    venc = _make_encoder(('x264enc', 'openh264enc'), 'stream_venc')
    h264parse = Gst.ElementFactory.make('h264parse', 'stream_h264parse')
    a_queue = Gst.ElementFactory.make('queue', 'stream_a_queue')
    aenc = _make_encoder(('avenc_aac', 'voaacenc', 'fdkaacenc'), 'stream_aenc')
    mpegtsmux = Gst.ElementFactory.make('mpegtsmux', 'stream_mpegtsmux')
    hlssink = Gst.ElementFactory.make('hlssink2', 'stream_hls_sink')

    if not all([v_queue, h264parse, a_queue, mpegtsmux, hlssink]):
        raise RuntimeError('Failed to create HLS streaming elements')

    _configure_video_encoder(venc, video_bitrate=video_bitrate)
    _configure_audio_encoder(aenc, audio_bitrate=audio_bitrate)

    hlssink.set_property('location', str(segment_pattern))
    hlssink.set_property('playlist-location', str(playlist_path))
    hlssink.set_property('target-duration', 2)
    hlssink.set_property('max-files', 0)

    elements = [v_queue, venc, h264parse, a_queue, aenc, mpegtsmux, hlssink]

    if not v_queue.link(venc):
        raise RuntimeError('Failed to link HLS video queue -> encoder')
    if not venc.link(h264parse):
        raise RuntimeError('Failed to link HLS video encoder -> h264parse')

    mux_video_pad = mpegtsmux.get_request_pad('sink_%d')
    mux_audio_pad = mpegtsmux.get_request_pad('sink_%d')
    if mux_video_pad is None or mux_audio_pad is None:
        raise RuntimeError('Failed to request mpegtsmux pads')

    if h264parse.get_static_pad('src').link(mux_video_pad) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link h264parse -> mpegtsmux video pad')

    if not a_queue.link(aenc):
        raise RuntimeError('Failed to link HLS audio queue -> encoder')

    if aenc.get_static_pad('src').link(mux_audio_pad) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link audio encoder -> mpegtsmux audio pad')

    if not mpegtsmux.link(hlssink):
        raise RuntimeError('Failed to link mpegtsmux -> hlssink2')

    return StreamingBranch(
        destination_type=DestinationType.HLS,
        destination_url=str(playlist_path),
        output_path=output_dir,
        elements=elements,
        mux=mpegtsmux,
        sink=hlssink,
    )


def build_streaming_branch(
    *,
    destination_type: str,
    destination_url: str,
    output_dir: Path | None,
    video_bitrate: int,
    audio_bitrate: int,
) -> StreamingBranch:
    if destination_type == DestinationType.RTMP:
        if not destination_url:
            raise ValueError('RTMP destination_url is required')
        return build_rtmp_streaming_branch(
            destination_url=destination_url,
            video_bitrate=video_bitrate,
            audio_bitrate=audio_bitrate,
        )

    if destination_type == DestinationType.HLS:
        if output_dir is None:
            raise ValueError('HLS output_dir is required')
        return build_hls_streaming_branch(
            output_dir=output_dir,
            video_bitrate=video_bitrate,
            audio_bitrate=audio_bitrate,
        )

    raise ValueError(f'Unsupported destination type: {destination_type}')


def attach_stream_to_tees(
    branch: StreamingBranch,
    *,
    video_tee: Gst.Element,
    audio_tee: Gst.Element,
) -> None:
    """Link the streaming branch to compositor output tees."""
    video_tee_pad = video_tee.get_request_pad('src_%u')
    audio_tee_pad = audio_tee.get_request_pad('src_%u')
    if video_tee_pad is None or audio_tee_pad is None:
        raise RuntimeError('Failed to request tee pads for streaming')

    v_queue = branch.elements[0]
    a_queue = branch.elements[3]

    if video_tee_pad.link(v_queue.get_static_pad('sink')) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link video tee -> streaming queue')

    if audio_tee_pad.link(a_queue.get_static_pad('sink')) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link audio tee -> streaming queue')

    branch.video_tee_pad = video_tee_pad
    branch.audio_tee_pad = audio_tee_pad


def finalize_hls_stream(branch: StreamingBranch, pipeline: Gst.Pipeline, *, timeout_sec: float) -> None:
    """Send EOS so hlssink2 finalizes the playlist."""
    if branch.destination_type != DestinationType.HLS or branch.sink is None:
        return

    branch.sink.send_event(Gst.Event.new_eos())

    bus = pipeline.get_bus()
    deadline = Gst.util_get_timestamp() + int(timeout_sec * Gst.SECOND)

    while True:
        message = bus.timed_pop_filtered(
            max(deadline - Gst.util_get_timestamp(), 0),
            Gst.MessageType.EOS | Gst.MessageType.ERROR,
        )
        if message is None:
            logger.warning('Timed out waiting for HLS stream EOS; playlist may be incomplete')
            break

        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            raise RuntimeError(f'HLS streaming pipeline error: {err} ({debug})')

        if message.type == Gst.MessageType.EOS and message.src == branch.sink:
            break


def teardown_streaming_branch(
    branch: StreamingBranch,
    pipeline: Gst.Pipeline,
    *,
    video_tee: Gst.Element,
    audio_tee: Gst.Element,
) -> None:
    """Remove streaming elements and release tee pads."""
    if branch.video_tee_pad is not None:
        branch.video_tee_pad.unlink(branch.elements[0].get_static_pad('sink'))
        video_tee.release_request_pad(branch.video_tee_pad)
        branch.video_tee_pad = None

    if branch.audio_tee_pad is not None:
        branch.audio_tee_pad.unlink(branch.elements[3].get_static_pad('sink'))
        audio_tee.release_request_pad(branch.audio_tee_pad)
        branch.audio_tee_pad = None

    for element in branch.elements:
        element.set_state(Gst.State.NULL)
        pipeline.remove(element)
