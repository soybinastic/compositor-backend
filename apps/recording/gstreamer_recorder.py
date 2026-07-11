"""GStreamer recording branch: encodes compositor output to MP4."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass
class RecordingBranch:
    file_path: Path
    elements: list[Gst.Element] = field(default_factory=list)
    mp4mux: Gst.Element | None = None
    filesink: Gst.Element | None = None
    video_tee_pad: Gst.Pad | None = None
    audio_tee_pad: Gst.Pad | None = None


def _make_encoder(factory_names: tuple[str, ...], name: str) -> Gst.Element:
    for factory_name in factory_names:
        element = Gst.ElementFactory.make(factory_name, name)
        if element is not None:
            return element
    raise RuntimeError(f'No GStreamer encoder available from {factory_names}')


def build_recording_branch(
    *,
    file_path: Path,
    video_bitrate: int,
    audio_bitrate: int,
) -> RecordingBranch:
    """Build an MP4 recording subgraph (not yet linked to the pipeline)."""
    mp4mux = Gst.ElementFactory.make('mp4mux', 'rec_mux')
    filesink = Gst.ElementFactory.make('filesink', 'rec_sink')
    v_queue = Gst.ElementFactory.make('queue', 'rec_v_queue')
    venc = _make_encoder(('x264enc', 'openh264enc'), 'rec_venc')
    h264parse = Gst.ElementFactory.make('h264parse', 'rec_h264parse')
    a_queue = Gst.ElementFactory.make('queue', 'rec_a_queue')
    aenc = _make_encoder(('avenc_aac', 'voaacenc', 'fdkaacenc'), 'rec_aenc')

    if not all([mp4mux, filesink, v_queue, h264parse, a_queue]):
        raise RuntimeError('Failed to create recording pipeline elements')

    filesink.set_property('location', str(file_path))
    filesink.set_property('async', False)

    if venc.get_factory().get_name() == 'x264enc':
        venc.set_property('speed-preset', 'ultrafast')
        venc.set_property('tune', 'zerolatency')
        venc.set_property('bitrate', max(video_bitrate // 1000, 500))
    elif venc.get_factory().get_name() == 'openh264enc':
        venc.set_property('bitrate', video_bitrate)

    if aenc.get_factory().get_name() == 'avenc_aac':
        aenc.set_property('bitrate', audio_bitrate)

    elements = [v_queue, venc, h264parse, a_queue, aenc, mp4mux, filesink]

    if not v_queue.link(venc):
        raise RuntimeError('Failed to link recording video queue -> encoder')
    if not venc.link(h264parse):
        raise RuntimeError('Failed to link recording video encoder -> h264parse')

    mux_video_pad = mp4mux.get_request_pad('video_%u')
    mux_audio_pad = mp4mux.get_request_pad('audio_%u')
    if mux_video_pad is None or mux_audio_pad is None:
        raise RuntimeError('Failed to request mp4mux pads')

    if h264parse.get_static_pad('src').link(mux_video_pad) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link h264parse -> mp4mux video pad')

    if not a_queue.link(aenc):
        raise RuntimeError('Failed to link recording audio queue -> encoder')

    if aenc.get_static_pad('src').link(mux_audio_pad) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link audio encoder -> mp4mux audio pad')

    if not mp4mux.link(filesink):
        raise RuntimeError('Failed to link mp4mux -> filesink')

    return RecordingBranch(
        file_path=file_path,
        elements=elements,
        mp4mux=mp4mux,
        filesink=filesink,
    )


def attach_to_tees(
    branch: RecordingBranch,
    *,
    video_tee: Gst.Element,
    audio_tee: Gst.Element,
) -> None:
    """Link the recording branch to compositor output tees."""
    video_tee_pad = video_tee.get_request_pad('src_%u')
    audio_tee_pad = audio_tee.get_request_pad('src_%u')
    if video_tee_pad is None or audio_tee_pad is None:
        raise RuntimeError('Failed to request tee pads for recording')

    v_queue = branch.elements[0]
    a_queue = branch.elements[3]

    if video_tee_pad.link(v_queue.get_static_pad('sink')) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link video tee -> recording queue')

    if audio_tee_pad.link(a_queue.get_static_pad('sink')) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link audio tee -> recording queue')

    branch.video_tee_pad = video_tee_pad
    branch.audio_tee_pad = audio_tee_pad


def finalize_recording(branch: RecordingBranch, pipeline: Gst.Pipeline, *, timeout_sec: float) -> None:
    """Send EOS and wait for the MP4 file to be finalized."""
    assert branch.mp4mux is not None

    branch.mp4mux.send_event(Gst.Event.new_eos())

    bus = pipeline.get_bus()
    deadline = Gst.util_get_timestamp() + int(timeout_sec * Gst.SECOND)

    while True:
        message = bus.timed_pop_filtered(
            max(deadline - Gst.util_get_timestamp(), 0),
            Gst.MessageType.EOS | Gst.MessageType.ERROR,
        )
        if message is None:
            logger.warning('Timed out waiting for recording EOS; file may be incomplete')
            break

        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            raise RuntimeError(f'Recording pipeline error: {err} ({debug})')

        if message.type == Gst.MessageType.EOS:
            source = message.src
            if source in (branch.mp4mux, branch.filesink):
                break


def teardown_recording_branch(
    branch: RecordingBranch,
    pipeline: Gst.Pipeline,
    *,
    video_tee: Gst.Element,
    audio_tee: Gst.Element,
) -> None:
    """Remove recording elements and release tee pads."""
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
