"""
GStreamer streaming branches: RTMP and HLS egress from compositor output.

Twitch / YouTube / custom RTMP destinations consume the same composited A/V as
recording. The branch must be wired like the recording path:

  1. Create elements (do not link yet)
  2. pipeline.add(...)
  3. Link the encode/mux graph
  4. Attach to video/audio tees
  5. sync_state_with_parent downstream → upstream

Linking before add left convert/encoder pads unnegotiated — the API returned
201 "Streaming started" while rtmpsink published little or no media, so Twitch
never went live (FFmpeg to the same URL worked).
"""

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
    """Live egress subgraph attached to compositor video/audio tees."""

    destination_type: str
    destination_url: str
    output_path: Path | None
    elements: list[Gst.Element] = field(default_factory=list)
    video_queue: Gst.Element | None = None
    audio_queue: Gst.Element | None = None
    video_chain: list[Gst.Element] = field(default_factory=list)
    audio_chain: list[Gst.Element] = field(default_factory=list)
    h264parse: Gst.Element | None = None
    aacparse: Gst.Element | None = None
    mux: Gst.Element | None = None
    sink: Gst.Element | None = None
    video_tee_pad: Gst.Pad | None = None
    audio_tee_pad: Gst.Pad | None = None
    _graph_linked: bool = field(default=False, repr=False)


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


def _configure_streaming_queue(queue: Gst.Element) -> None:
    """Bounded leaky queues so a slow RTMP sink cannot stall the compositor."""
    queue.set_property('leaky', 2)
    queue.set_property('max-size-time', 2 * Gst.SECOND)
    queue.set_property('max-size-buffers', 0)
    queue.set_property('max-size-bytes', 0)


def _configure_video_encoder(venc: Gst.Element, *, video_bitrate: int) -> None:
    """
    Configure H.264 for live RTMP platforms (Twitch, YouTube, etc.).

    Twitch expects Annex-B/AVC-compatible H.264 with regular keyframes (~2s)
    and 4:2:0 chroma (ensured via videoconvert → I420 before the encoder).
    """
    factory_name = venc.get_factory().get_name()
    if factory_name == 'x264enc':
        venc.set_property('speed-preset', 'ultrafast')
        venc.set_property('tune', 'zerolatency')
        venc.set_property('key-int-max', 60)
        venc.set_property('bitrate', max(video_bitrate // 1000, 500))
        if venc.find_property('byte-stream') is not None:
            venc.set_property('byte-stream', False)
    elif factory_name == 'openh264enc':
        venc.set_property('bitrate', video_bitrate)


def _configure_audio_encoder(aenc: Gst.Element, *, audio_bitrate: int) -> None:
    if aenc.get_factory().get_name() == 'avenc_aac':
        aenc.set_property('bitrate', audio_bitrate)


def _configure_h264parse(h264parse: Gst.Element) -> None:
    """Re-emit SPS/PPS so late joiners / ingest servers can decode immediately."""
    if h264parse.find_property('config-interval') is not None:
        h264parse.set_property('config-interval', -1)


def _build_av_encode_elements(
    *,
    video_bitrate: int,
    audio_bitrate: int,
) -> tuple[
    Gst.Element,
    list[Gst.Element],
    Gst.Element,
    list[Gst.Element],
    Gst.Element,
    Gst.Element,
]:
    """
    Create shared video/audio encode chains (unlinked).

    Returns (v_queue, video_chain, a_queue, audio_chain, h264parse, aacparse).
    """
    v_queue = Gst.ElementFactory.make('queue', 'stream_v_queue')
    v_convert = Gst.ElementFactory.make('videoconvert', 'stream_v_convert')
    venc = _make_encoder(('x264enc', 'openh264enc'), 'stream_venc')
    h264parse = Gst.ElementFactory.make('h264parse', 'stream_h264parse')
    a_queue = Gst.ElementFactory.make('queue', 'stream_a_queue')
    a_convert = Gst.ElementFactory.make('audioconvert', 'stream_a_convert')
    a_resample = Gst.ElementFactory.make('audioresample', 'stream_a_resample')
    aenc = _make_encoder(('avenc_aac', 'voaacenc', 'fdkaacenc'), 'stream_aenc')
    aacparse = Gst.ElementFactory.make('aacparse', 'stream_aacparse')

    if not all(
        [v_queue, v_convert, h264parse, a_queue, a_convert, a_resample, aacparse]
    ):
        raise RuntimeError('Failed to create streaming encode elements')

    _configure_streaming_queue(v_queue)
    _configure_streaming_queue(a_queue)
    _configure_video_encoder(venc, video_bitrate=video_bitrate)
    _configure_audio_encoder(aenc, audio_bitrate=audio_bitrate)
    _configure_h264parse(h264parse)

    video_chain = [v_queue, v_convert, venc, h264parse]
    audio_chain = [a_queue, a_convert, a_resample, aenc, aacparse]
    return v_queue, video_chain, a_queue, audio_chain, h264parse, aacparse


def build_rtmp_streaming_branch(
    *,
    destination_url: str,
    video_bitrate: int,
    audio_bitrate: int,
) -> StreamingBranch:
    """
    Create an FLV/RTMP live streaming subgraph (elements only; link later).

    Destination URL form for Twitch:
      rtmp://live.twitch.tv/app/<stream_key>
    """
    (
        v_queue,
        video_chain,
        a_queue,
        audio_chain,
        h264parse,
        aacparse,
    ) = _build_av_encode_elements(
        video_bitrate=video_bitrate,
        audio_bitrate=audio_bitrate,
    )
    flvmux = Gst.ElementFactory.make('flvmux', 'stream_flvmux')
    sink = _make_sink(('rtmpsink', 'rtmp2sink'), 'stream_rtmp_sink')

    if flvmux is None:
        raise RuntimeError('Failed to create flvmux for RTMP streaming')

    flvmux.set_property('streamable', True)
    sink.set_property('location', destination_url.strip())
    if sink.get_factory().get_name() == 'rtmpsink':
        sink.set_property('sync', False)
        sink.set_property('async', False)

    elements = [*video_chain, *audio_chain, flvmux, sink]

    return StreamingBranch(
        destination_type=DestinationType.RTMP,
        destination_url=destination_url.strip(),
        output_path=None,
        elements=elements,
        video_queue=v_queue,
        audio_queue=a_queue,
        video_chain=video_chain,
        audio_chain=audio_chain,
        h264parse=h264parse,
        aacparse=aacparse,
        mux=flvmux,
        sink=sink,
    )


def build_hls_streaming_branch(
    *,
    output_dir: Path,
    video_bitrate: int,
    audio_bitrate: int,
) -> StreamingBranch:
    """
    Create a local HLS output subgraph (elements only; link later).

    hlssink2 accepts encoded H.264/AAC on separate request pads (no mpegtsmux).
    Writes playlist.m3u8 + segment_*.ts under output_dir for local preview.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    segment_pattern = output_dir / 'segment_%05d.ts'
    playlist_path = output_dir / 'playlist.m3u8'

    (
        v_queue,
        video_chain,
        a_queue,
        audio_chain,
        h264parse,
        aacparse,
    ) = _build_av_encode_elements(
        video_bitrate=video_bitrate,
        audio_bitrate=audio_bitrate,
    )
    hlssink = Gst.ElementFactory.make('hlssink2', 'stream_hls_sink')
    if hlssink is None:
        raise RuntimeError('Failed to create hlssink2 for HLS streaming')

    hlssink.set_property('location', str(segment_pattern))
    hlssink.set_property('playlist-location', str(playlist_path))
    hlssink.set_property('target-duration', 2)
    hlssink.set_property('max-files', 0)

    elements = [*video_chain, *audio_chain, hlssink]

    return StreamingBranch(
        destination_type=DestinationType.HLS,
        destination_url=str(playlist_path),
        output_path=output_dir,
        elements=elements,
        video_queue=v_queue,
        audio_queue=a_queue,
        video_chain=video_chain,
        audio_chain=audio_chain,
        h264parse=h264parse,
        aacparse=aacparse,
        mux=None,
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
    """Factory: create an unlinked RTMP or HLS StreamingBranch."""
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


def _link_sequential(elements: list[Gst.Element], *, label: str) -> None:
    for upstream, downstream in zip(elements, elements[1:]):
        if not upstream.link(downstream):
            raise RuntimeError(
                f'Failed to link streaming {label} {upstream.name} -> {downstream.name}'
            )


def _link_streaming_graph(branch: StreamingBranch) -> None:
    """
    Link encode/mux/sink after elements are in the pipeline.

    Must not run before pipeline.add — see module docstring.

    RTMP: h264/aac → flvmux → rtmpsink
    HLS:  h264 → hlssink2.video, aac → hlssink2.audio
    """
    if branch._graph_linked:
        return
    assert branch.sink is not None
    assert branch.h264parse is not None
    assert branch.aacparse is not None

    _link_sequential(branch.video_chain, label='video')
    _link_sequential(branch.audio_chain, label='audio')

    if branch.destination_type == DestinationType.RTMP:
        assert branch.mux is not None
        mux_video_pad = branch.mux.get_request_pad('video')
        mux_audio_pad = branch.mux.get_request_pad('audio')
        if mux_video_pad is None or mux_audio_pad is None:
            raise RuntimeError('Failed to request flvmux pads for RTMP streaming')

        if (
            branch.h264parse.get_static_pad('src').link(mux_video_pad)
            != Gst.PadLinkReturn.OK
        ):
            raise RuntimeError('Failed to link h264parse -> flvmux video pad')

        if (
            branch.aacparse.get_static_pad('src').link(mux_audio_pad)
            != Gst.PadLinkReturn.OK
        ):
            raise RuntimeError('Failed to link aacparse -> flvmux audio pad')

        if not branch.mux.link(branch.sink):
            raise RuntimeError('Failed to link flvmux -> rtmpsink')
    else:
        # hlssink2: separate encoded A/V request pads (not a single mpegts sink).
        video_pad = branch.sink.get_request_pad('video')
        audio_pad = branch.sink.get_request_pad('audio')
        if video_pad is None or audio_pad is None:
            raise RuntimeError('Failed to request hlssink2 pads')

        if (
            branch.h264parse.get_static_pad('src').link(video_pad)
            != Gst.PadLinkReturn.OK
        ):
            raise RuntimeError('Failed to link h264parse -> hlssink2 video pad')

        if (
            branch.aacparse.get_static_pad('src').link(audio_pad)
            != Gst.PadLinkReturn.OK
        ):
            raise RuntimeError('Failed to link aacparse -> hlssink2 audio pad')

    branch._graph_linked = True


def attach_stream_to_tees(
    branch: StreamingBranch,
    *,
    video_tee: Gst.Element,
    audio_tee: Gst.Element,
) -> None:
    """Request tee src pads and link them into the streaming queues."""
    assert branch.video_queue is not None
    assert branch.audio_queue is not None

    video_tee_pad = video_tee.get_request_pad('src_%u')
    audio_tee_pad = audio_tee.get_request_pad('src_%u')
    if video_tee_pad is None or audio_tee_pad is None:
        raise RuntimeError('Failed to request tee pads for streaming')

    if video_tee_pad.link(branch.video_queue.get_static_pad('sink')) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link video tee -> streaming queue')

    if audio_tee_pad.link(branch.audio_queue.get_static_pad('sink')) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link audio tee -> streaming queue')

    branch.video_tee_pad = video_tee_pad
    branch.audio_tee_pad = audio_tee_pad


def start_streaming_on_pipeline(
    pipeline: Gst.Pipeline,
    branch: StreamingBranch,
    *,
    video_tee: Gst.Element,
    audio_tee: Gst.Element,
) -> None:
    """
    Add, link, and activate a streaming branch on a running compositor pipeline.

    Order matches start_recording_on_pipeline: add → link graph → attach tees →
    sync states reverse (downstream first).
    """
    for element in branch.elements:
        pipeline.add(element)

    _link_streaming_graph(branch)
    attach_stream_to_tees(branch, video_tee=video_tee, audio_tee=audio_tee)

    for element in reversed(branch.elements):
        if not element.sync_state_with_parent():
            raise RuntimeError(
                f'Failed to sync streaming element {element.get_name()} with pipeline'
            )

    logger.info(
        'Streaming branch linked to compositor tees (%s -> %s)',
        branch.destination_type,
        branch.destination_url,
    )


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
    """Unlink from tees, NULL elements, and remove them from the pipeline."""
    assert branch.video_queue is not None
    assert branch.audio_queue is not None

    if branch.video_tee_pad is not None:
        v_sink = branch.video_queue.get_static_pad('sink')
        if v_sink is not None and branch.video_tee_pad.is_linked():
            branch.video_tee_pad.unlink(v_sink)
        video_tee.release_request_pad(branch.video_tee_pad)
        branch.video_tee_pad = None

    if branch.audio_tee_pad is not None:
        a_sink = branch.audio_queue.get_static_pad('sink')
        if a_sink is not None and branch.audio_tee_pad.is_linked():
            branch.audio_tee_pad.unlink(a_sink)
        audio_tee.release_request_pad(branch.audio_tee_pad)
        branch.audio_tee_pad = None

    for element in branch.elements:
        element.set_state(Gst.State.NULL)
        pipeline.remove(element)
