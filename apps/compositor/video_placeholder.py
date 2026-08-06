"""Build live per-tile camera-off placeholder video for the compositor."""

from __future__ import annotations

import logging

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

logger = logging.getLogger(__name__)


def placeholder_initials(display_name: str) -> str:
    cleaned = (display_name or '').replace(' (You)', '').strip()
    if not cleaned:
        return '?'
    parts = [part for part in cleaned.split() if part]
    letters = ''.join(part[0] for part in parts)[:2]
    return letters.upper() or '?'


def build_participant_placeholder_chain(
    *,
    peer_id: str,
    display_name: str,
    width: int,
    height: int,
    fps: int,
    ingest_tail: list[Gst.Element],
) -> tuple[list[Gst.Element], Gst.Element]:
    """
    Live black plate + initials/name into the same ingest tail used by RTP video.

    Returns (all_elements, output_element) where output links to compositor sink.
    """
    fps = max(1, int(fps))
    safe_id = peer_id.replace('/', '_').replace(' ', '_')
    initials = placeholder_initials(display_name)
    label = (display_name or peer_id).replace(' (You)', '').strip() or peer_id
    overlay_text = f'{initials}\n{label}'

    src = Gst.ElementFactory.make('videotestsrc', f'ph_src_{safe_id}')
    caps = Gst.ElementFactory.make('capsfilter', f'ph_caps_{safe_id}')
    text = Gst.ElementFactory.make('textoverlay', f'ph_text_{safe_id}')
    convert = Gst.ElementFactory.make('videoconvert', f'ph_convert_{safe_id}')

    if not all([src, caps, text, convert, *ingest_tail]):
        raise RuntimeError(f'Failed to create placeholder chain for {peer_id}')

    src.set_property('is-live', True)
    src.set_property('pattern', 2)  # black
    # Align timestamps with the pipeline clock so the live compositor/encoder
    # keep flowing after a mid-stream pad swap (avoids silent RTMP stalls).
    if src.find_property('do-timestamp') is not None:
        src.set_property('do-timestamp', True)
    caps.set_property(
        'caps',
        Gst.Caps.from_string(
            f'video/x-raw,format=I420,width={max(2, width)},height={max(2, height)},'
            f'framerate={fps}/1'
        ),
    )
    text.set_property('text', overlay_text)
    text.set_property('valignment', 1)  # center
    text.set_property('halignment', 1)  # center
    text.set_property('font-desc', 'Sans Bold 48')
    text.set_property('shaded-background', True)

    elements = [src, caps, text, convert, *ingest_tail]
    return elements, ingest_tail[-1]
