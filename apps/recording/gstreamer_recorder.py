"""GStreamer recording branch: encodes compositor output to MP4."""

from __future__ import annotations

import logging
import threading
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
    video_queue: Gst.Element | None = None
    audio_queue: Gst.Element | None = None
    video_chain: list[Gst.Element] = field(default_factory=list)
    audio_chain: list[Gst.Element] = field(default_factory=list)
    h264parse: Gst.Element | None = None
    aacparse: Gst.Element | None = None
    mp4mux: Gst.Element | None = None
    filesink: Gst.Element | None = None
    video_tee_pad: Gst.Pad | None = None
    audio_tee_pad: Gst.Pad | None = None
    video_buffers: int = 0
    audio_buffers: int = 0
    _video_probe_id: int | None = field(default=None, repr=False)
    _audio_probe_id: int | None = field(default=None, repr=False)
    _video_counter: list[int] | None = field(default=None, repr=False)
    _audio_counter: list[int] | None = field(default=None, repr=False)
    _graph_linked: bool = field(default=False, repr=False)


def _make_encoder(factory_names: tuple[str, ...], name: str) -> Gst.Element:
    for factory_name in factory_names:
        element = Gst.ElementFactory.make(factory_name, name)
        if element is not None:
            return element
    raise RuntimeError(f'No GStreamer encoder available from {factory_names}')


def _configure_recording_queue(queue: Gst.Element) -> None:
    """Prevent a stalled recording branch from blocking the live compositor tees."""
    queue.set_property('leaky', 2)  # downstream
    queue.set_property('max-size-buffers', 0)
    queue.set_property('max-size-time', 2 * Gst.SECOND)
    queue.set_property('max-size-bytes', 0)


def _set_element_state(element: Gst.Element, state: Gst.State, *, label: str) -> None:
    ret = element.set_state(state)
    if ret == Gst.StateChangeReturn.FAILURE:
        raise RuntimeError(f'Failed to set {label} to {state.value_nick}')

    if ret == Gst.StateChangeReturn.ASYNC:
        change_return, current_state, _pending = element.get_state(Gst.CLOCK_TIME_NONE)
        if change_return == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(f'Failed to reach {state.value_nick} for {label}')
        if current_state != state:
            raise RuntimeError(
                f'{label} reached {current_state.value_nick} instead of {state.value_nick}'
            )


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


def build_recording_branch(
    *,
    file_path: Path,
    video_bitrate: int,
    audio_bitrate: int,
    fps: int,
) -> RecordingBranch:
    """Create recording elements (link only after pipeline.add)."""
    v_queue = Gst.ElementFactory.make('queue', 'rec_v_queue')
    v_convert = Gst.ElementFactory.make('videoconvert', 'rec_v_convert')
    venc = _make_encoder(('x264enc', 'openh264enc'), 'rec_venc')
    h264parse = Gst.ElementFactory.make('h264parse', 'rec_h264parse')
    a_queue = Gst.ElementFactory.make('queue', 'rec_a_queue')
    a_convert = Gst.ElementFactory.make('audioconvert', 'rec_a_convert')
    a_resample = Gst.ElementFactory.make('audioresample', 'rec_a_resample')
    aenc = _make_encoder(('avenc_aac', 'voaacenc', 'fdkaacenc'), 'rec_aenc')
    aacparse = Gst.ElementFactory.make('aacparse', 'rec_aacparse')
    mp4mux = Gst.ElementFactory.make('mp4mux', 'rec_mux')
    filesink = Gst.ElementFactory.make('filesink', 'rec_sink')

    if not all(
        [
            v_queue,
            v_convert,
            h264parse,
            a_queue,
            a_convert,
            a_resample,
            aacparse,
            mp4mux,
            filesink,
        ]
    ):
        raise RuntimeError('Failed to create recording pipeline elements')

    filesink.set_property('location', str(file_path))
    filesink.set_property('async', False)
    filesink.set_property('sync', False)
    # Prefers a usable file even if final EOS is delayed.
    if mp4mux.find_property('streamable') is not None:
        mp4mux.set_property('streamable', True)
    if mp4mux.find_property('reserved-max-duration') is not None:
        mp4mux.set_property('reserved-max-duration', 3 * 60 * 60 * Gst.SECOND)

    _configure_recording_queue(v_queue)
    _configure_recording_queue(a_queue)
    _configure_video_encoder(venc, video_bitrate=video_bitrate)
    _configure_audio_encoder(aenc, audio_bitrate=audio_bitrate)
    _ = fps

    video_chain = [v_queue, v_convert, venc, h264parse]
    audio_chain = [a_queue, a_convert, a_resample, aenc, aacparse]
    elements = [*video_chain, *audio_chain, mp4mux, filesink]

    return RecordingBranch(
        file_path=file_path,
        elements=elements,
        video_queue=v_queue,
        audio_queue=a_queue,
        video_chain=video_chain,
        audio_chain=audio_chain,
        h264parse=h264parse,
        aacparse=aacparse,
        mp4mux=mp4mux,
        filesink=filesink,
    )


def _link_recording_graph(branch: RecordingBranch) -> None:
    """Link recording elements that are already in the pipeline."""
    if branch._graph_linked:
        return
    assert branch.mp4mux is not None
    assert branch.filesink is not None
    assert branch.h264parse is not None
    assert branch.aacparse is not None

    for upstream, downstream in zip(branch.video_chain, branch.video_chain[1:]):
        if not upstream.link(downstream):
            raise RuntimeError(
                f'Failed to link recording video {upstream.name} -> {downstream.name}'
            )

    for upstream, downstream in zip(branch.audio_chain, branch.audio_chain[1:]):
        if not upstream.link(downstream):
            raise RuntimeError(
                f'Failed to link recording audio {upstream.name} -> {downstream.name}'
            )

    mux_video_pad = branch.mp4mux.get_request_pad('video_%u')
    mux_audio_pad = branch.mp4mux.get_request_pad('audio_%u')
    if mux_video_pad is None or mux_audio_pad is None:
        raise RuntimeError('Failed to request mp4mux pads')

    if branch.h264parse.get_static_pad('src').link(mux_video_pad) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link h264parse -> mp4mux video pad')

    if branch.aacparse.get_static_pad('src').link(mux_audio_pad) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link aacparse -> mp4mux audio pad')

    if not branch.mp4mux.link(branch.filesink):
        raise RuntimeError('Failed to link mp4mux -> filesink')

    branch._graph_linked = True


def attach_to_tees(
    branch: RecordingBranch,
    *,
    video_tee: Gst.Element,
    audio_tee: Gst.Element,
) -> None:
    """Link the recording branch to compositor output tees and count buffers."""
    assert branch.video_queue is not None
    assert branch.audio_queue is not None

    video_tee_pad = video_tee.get_request_pad('src_%u')
    audio_tee_pad = audio_tee.get_request_pad('src_%u')
    if video_tee_pad is None or audio_tee_pad is None:
        raise RuntimeError('Failed to request tee pads for recording')

    v_sink = branch.video_queue.get_static_pad('sink')
    a_sink = branch.audio_queue.get_static_pad('sink')
    if v_sink is None or a_sink is None:
        raise RuntimeError('Failed to get recording queue sink pads')

    if video_tee_pad.link(v_sink) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link video tee -> recording queue')
    if audio_tee_pad.link(a_sink) != Gst.PadLinkReturn.OK:
        raise RuntimeError('Failed to link audio tee -> recording queue')

    branch.video_tee_pad = video_tee_pad
    branch.audio_tee_pad = audio_tee_pad

    video_counter = [0]
    audio_counter = [0]

    def _make_counter_probe(counter: list[int]):
        def _probe(_pad: Gst.Pad, _info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
            counter[0] += 1
            return Gst.PadProbeReturn.OK

        return _probe

    branch._video_probe_id = v_sink.add_probe(
        Gst.PadProbeType.BUFFER,
        _make_counter_probe(video_counter),
        None,
    )
    branch._audio_probe_id = a_sink.add_probe(
        Gst.PadProbeType.BUFFER,
        _make_counter_probe(audio_counter),
        None,
    )
    branch._video_counter = video_counter
    branch._audio_counter = audio_counter


def activate_recording_branch(branch: RecordingBranch) -> None:
    """Deprecated: use start_recording_on_pipeline()."""
    for element in reversed(branch.elements):
        _set_element_state(element, Gst.State.PLAYING, label=element.get_name())


def start_recording_on_pipeline(
    pipeline: Gst.Pipeline,
    branch: RecordingBranch,
    *,
    video_tee: Gst.Element,
    audio_tee: Gst.Element,
) -> None:
    """
    Add, link, and activate the recording branch on a running compositor pipeline.

    Elements must be added to the pipeline before linking — linking orphans
    first left convert/encoder pads unnegotiated (queues filled, MP4 stayed 0 bytes).
    """
    for element in branch.elements:
        pipeline.add(element)

    _link_recording_graph(branch)
    attach_to_tees(branch, video_tee=video_tee, audio_tee=audio_tee)

    # Downstream → upstream into an already-PLAYING parent.
    for element in reversed(branch.elements):
        if not element.sync_state_with_parent():
            raise RuntimeError(
                f'Failed to sync recording element {element.get_name()} with pipeline'
            )

    logger.info(
        'Recording branch linked to compositor tees for %s',
        branch.file_path,
    )


def _refresh_buffer_counts(branch: RecordingBranch) -> None:
    if branch._video_counter is not None:
        branch.video_buffers = branch._video_counter[0]
    if branch._audio_counter is not None:
        branch.audio_buffers = branch._audio_counter[0]


def _remove_buffer_probes(branch: RecordingBranch) -> None:
    if branch.video_queue is not None and branch._video_probe_id is not None:
        sink = branch.video_queue.get_static_pad('sink')
        if sink is not None:
            sink.remove_probe(branch._video_probe_id)
        branch._video_probe_id = None

    if branch.audio_queue is not None and branch._audio_probe_id is not None:
        sink = branch.audio_queue.get_static_pad('sink')
        if sink is not None:
            sink.remove_probe(branch._audio_probe_id)
        branch._audio_probe_id = None


def _unlink_recording_from_tees(branch: RecordingBranch) -> None:
    """Disconnect the recording branch from compositor output tees."""
    assert branch.video_queue is not None
    assert branch.audio_queue is not None

    if branch.video_tee_pad is not None:
        v_sink = branch.video_queue.get_static_pad('sink')
        if v_sink is not None and branch.video_tee_pad.is_linked():
            branch.video_tee_pad.unlink(v_sink)

    if branch.audio_tee_pad is not None:
        a_sink = branch.audio_queue.get_static_pad('sink')
        if a_sink is not None and branch.audio_tee_pad.is_linked():
            branch.audio_tee_pad.unlink(a_sink)


def _is_expected_finalize_error(err: Exception, debug: str | None) -> bool:
    """True for teardown races after intentional tee unlink / EOS flush."""
    combined = f'{err} {debug or ""}'.lower()
    return 'not-linked' in combined


def _arm_recording_eos_probes(
    branch: RecordingBranch,
) -> list[tuple[Gst.Pad, int]]:
    """
    Block tee→recording pads and push EOS downstream once.

    Must run while still linked: queue.send_event(EOS) fails after unlink
    (no upstream peer), which left filesink waiting forever.
    """
    probe_ids: list[tuple[Gst.Pad, int]] = []

    for tee_pad in (branch.video_tee_pad, branch.audio_tee_pad):
        if tee_pad is None:
            continue

        state = {'eos_sent': False}

        def _probe(
            pad: Gst.Pad,
            _info: Gst.PadProbeInfo,
            user_data: dict[str, bool],
        ) -> Gst.PadProbeReturn:
            if not user_data['eos_sent']:
                user_data['eos_sent'] = True
                pad.push_event(Gst.Event.new_eos())
            return Gst.PadProbeReturn.DROP

        probe_id = tee_pad.add_probe(
            Gst.PadProbeType.BLOCK | Gst.PadProbeType.DATA_DOWNSTREAM,
            _probe,
            state,
        )
        probe_ids.append((tee_pad, probe_id))

    return probe_ids


def _send_eos_to_recording_branch(branch: RecordingBranch) -> None:
    """Inject EOS at recording queue sinks (works while pads are linked)."""
    assert branch.video_queue is not None
    assert branch.audio_queue is not None

    for queue, label in (
        (branch.video_queue, 'video'),
        (branch.audio_queue, 'audio'),
    ):
        sink = queue.get_static_pad('sink')
        if sink is None:
            logger.warning('No %s recording queue sink pad for EOS', label)
            continue
        if not sink.send_event(Gst.Event.new_eos()):
            logger.warning('Failed to send EOS to recording %s queue sink', label)


def finalize_recording(
    branch: RecordingBranch,
    pipeline: Gst.Pipeline,
    *,
    timeout_sec: float,
    composited_frames: int = 0,
    participant_ingest: str = '',
) -> None:
    """Flush the recording subgraph with EOS and wait for MP4 finalization."""
    assert branch.mp4mux is not None
    assert branch.filesink is not None

    _refresh_buffer_counts(branch)
    logger.info(
        'Finalizing recording: video_buffers=%s audio_buffers=%s composited_frames=%s '
        'participants=[%s] -> %s',
        branch.video_buffers,
        branch.audio_buffers,
        composited_frames,
        participant_ingest,
        branch.file_path,
    )

    if branch.video_buffers == 0 and branch.audio_buffers == 0:
        raise RuntimeError(
            'Recording received no media from compositor output '
            f'(recording buffers=0, composited_frames={composited_frames}, '
            f'ingest=[{participant_ingest}])'
        )

    watched_elements = set(branch.elements)
    filesink_eos = threading.Event()
    eos_probe_ids: list[tuple[Gst.Pad, int]] = []

    def _on_filesink_event(_pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
        event = info.get_event()
        if event is not None and event.type == Gst.EventType.EOS:
            filesink_eos.set()
        return Gst.PadProbeReturn.OK

    sink_pad = branch.filesink.get_static_pad('sink')
    if sink_pad is None:
        raise RuntimeError('Failed to get filesink sink pad')

    filesink_probe_id = sink_pad.add_probe(
        Gst.PadProbeType.EVENT_DOWNSTREAM,
        _on_filesink_event,
        None,
    )

    try:
        _remove_buffer_probes(branch)
        # EOS while still linked, dropping further live buffers on the tee pads.
        eos_probe_ids = _arm_recording_eos_probes(branch)
        _send_eos_to_recording_branch(branch)

        bus = pipeline.get_bus()
        deadline = Gst.util_get_timestamp() + int(timeout_sec * Gst.SECOND)

        while not filesink_eos.is_set():
            remaining = max(deadline - Gst.util_get_timestamp(), 0)
            if remaining == 0:
                size = branch.file_path.stat().st_size if branch.file_path.exists() else 0
                if size > 0:
                    logger.warning(
                        'Timed out waiting for recording EOS; keeping partial MP4 '
                        '(%s bytes) at %s',
                        size,
                        branch.file_path,
                    )
                    return
                raise RuntimeError(
                    'Timed out waiting for recording EOS; MP4 file may be incomplete'
                )

            message = bus.timed_pop_filtered(
                min(remaining, 200 * Gst.MSECOND),
                Gst.MessageType.ERROR,
            )
            if message is None:
                continue

            if message.type == Gst.MessageType.ERROR:
                source = message.src
                err, debug = message.parse_error()
                if _is_expected_finalize_error(err, debug):
                    logger.info(
                        'Ignoring expected recording finalize error from %s: %s (%s)',
                        getattr(source, 'name', source),
                        err,
                        debug,
                    )
                    continue
                if source in watched_elements:
                    raise RuntimeError(f'Recording pipeline error: {err} ({debug})')
                logger.warning(
                    'Ignoring non-recording pipeline error during finalize: %s (%s)',
                    err,
                    debug,
                )
    finally:
        for tee_pad, probe_id in eos_probe_ids:
            tee_pad.remove_probe(probe_id)
        _unlink_recording_from_tees(branch)
        sink_pad.remove_probe(filesink_probe_id)


def teardown_recording_branch(
    branch: RecordingBranch,
    pipeline: Gst.Pipeline,
    *,
    video_tee: Gst.Element,
    audio_tee: Gst.Element,
) -> None:
    """Remove recording elements and release tee pads."""
    _remove_buffer_probes(branch)
    _unlink_recording_from_tees(branch)

    if branch.video_tee_pad is not None:
        video_tee.release_request_pad(branch.video_tee_pad)
        branch.video_tee_pad = None

    if branch.audio_tee_pad is not None:
        audio_tee.release_request_pad(branch.audio_tee_pad)
        branch.audio_tee_pad = None

    for element in branch.elements:
        element.set_state(Gst.State.NULL)
        pipeline.remove(element)
