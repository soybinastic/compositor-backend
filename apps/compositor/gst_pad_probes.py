"""Shared GStreamer pad probes for compositor ingest branches."""

from __future__ import annotations

import logging
from typing import Callable

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

logger = logging.getLogger(__name__)


def make_running_time_offset_probe(
    pipeline: Gst.Pipeline,
    *,
    continuous: bool = True,
) -> Callable[..., Gst.PadProbeReturn]:
    """
    Keep buffer PTS aligned to pipeline running time.

    Mediasoup RTP and uridecodebin file sources emit timestamps that do not
    match the compositor/audiomixer clock. Without alignment, force-live
    aggregators hold or drop buffers even while decode keeps producing data.

    For live still graphics (appsrc do-timestamp), pass continuous=False —
    timestamps are already near running time after the first alignment.
    Continuous re-offset on a leaky still pad can jitter the mixer.
    """
    state = {'logged': False, 'applied': False}

    def _probe(pad: Gst.Pad, info: Gst.PadProbeInfo, _user_data) -> Gst.PadProbeReturn:
        if not continuous and state['applied']:
            return Gst.PadProbeReturn.OK

        buffer = info.get_buffer()
        if buffer is None or buffer.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK

        clock = pipeline.get_clock()
        if clock is None:
            return Gst.PadProbeReturn.OK

        running_time = clock.get_time() - pipeline.get_base_time()
        if running_time < 0:
            return Gst.PadProbeReturn.OK

        pad.set_offset(int(running_time) - int(buffer.pts))
        state['applied'] = True
        if not state['logged']:
            state['logged'] = True
            logger.info(
                'Applied running-time pad offset=%s on %s (continuous=%s)',
                pad.get_offset(),
                pad.get_path_string(),
                continuous,
            )
        return Gst.PadProbeReturn.OK

    return _probe
