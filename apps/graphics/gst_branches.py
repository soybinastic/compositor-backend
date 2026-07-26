"""GStreamer branch builders for graphics layers."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import gi
from PIL import Image

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

from apps.graphics.asset_cache import get_asset_cache
from apps.graphics.constants import VIDEO_EXTENSIONS
from apps.graphics.geometry import scale_to_box
from apps.graphics.renderers.pil_overlays import image_to_rgba_bytes
from apps.graphics.visibility import resolve_url

logger = logging.getLogger(__name__)

Gst.init(None)


@dataclass
class GraphicBranch:
    layer_key: str
    compositor_sink_pad: Gst.Pad
    elements: list[Gst.Element] = field(default_factory=list)
    signature: str = ''
    temp_paths: list[Path] = field(default_factory=list)
    geometry: tuple[int, int, int, int] = (0, 0, 1, 1)
    zorder: int = 0
    visible: bool = True
    ticker_width: int = 0
    ticker_direction: str = 'rtl'
    ticker_speed: float = 2.0
    # Live still pusher (appsrc). Stop before tearing down elements.
    appsrc: Gst.Element | None = None
    still_rgba: bytes | None = None
    still_width: int = 0
    still_height: int = 0
    still_fps: int = 30
    # Persistent live background: videotestsrc → gdkpixbufoverlay.
    bg_overlay: Gst.Element | None = None
    _bg_pixel_bytes: bytes | None = field(default=None, repr=False)
    _stop_push: threading.Event | None = field(default=None, repr=False)
    _push_thread: threading.Thread | None = field(default=None, repr=False)


def build_live_background_base(
    *,
    layer_key: str,
    width: int,
    height: int,
    fps: int,
) -> tuple[list[Gst.Element], Gst.Element, Gst.Element]:
    """
    videotestsrc (is-live, low-res) → videoscale → caps → gdkpixbufoverlay → videoconvert.

    A continuous live source keeps the force-live compositor healthy. Background
    images are applied by setting pixbuf on the overlay (alpha 0 when unused).
    Low-res black generation stays cheap on CPU encode paths.
    """
    fps = max(1, int(fps))
    src = Gst.ElementFactory.make('videotestsrc', f'{layer_key}_vtest')
    caps_in = Gst.ElementFactory.make('capsfilter', f'{layer_key}_caps_in')
    scale = Gst.ElementFactory.make('videoscale', f'{layer_key}_scale')
    caps_out = Gst.ElementFactory.make('capsfilter', f'{layer_key}_caps_out')
    overlay = Gst.ElementFactory.make('gdkpixbufoverlay', f'{layer_key}_pixbuf')
    convert = Gst.ElementFactory.make('videoconvert', f'{layer_key}_convert')
    if not all([src, caps_in, scale, caps_out, overlay, convert]):
        raise RuntimeError(f'Failed to create live background base for {layer_key}')

    src.set_property('is-live', True)
    src.set_property('pattern', 2)  # black
    # Tiny live source; compositor pad sizes to full canvas.
    caps_in.set_property(
        'caps',
        Gst.Caps.from_string(f'video/x-raw,width=320,height=180,framerate={fps}/1'),
    )
    caps_out.set_property(
        'caps',
        Gst.Caps.from_string(
            f'video/x-raw,width={width},height={height},framerate={fps}/1'
        ),
    )
    overlay.set_property('alpha', 0.0)
    return [src, caps_in, scale, caps_out, overlay, convert], convert, overlay


def content_signature(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def is_video_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in VIDEO_EXTENSIONS)


def load_image_from_url(url: str) -> Image.Image:
    data = get_asset_cache().fetch(url)
    return Image.open(io.BytesIO(data)).convert('RGBA')


def build_live_still_chain_from_image(
    *,
    layer_key: str,
    image: Image.Image,
    fps: int,
    target_w: int | None = None,
    target_h: int | None = None,
) -> tuple[list[Gst.Element], Gst.Element, Gst.Element, bytes, int, int]:
    """
    appsrc (live, timed) → videoconvert.

    A pusher thread feeds the same RGBA frame at `fps`. This matches the
    force-live compositor timing model used by cameras and avoids imagefreeze
    / identity sync stalls that starve RTMP.
    """
    if target_w and target_h:
        image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
    rgba, width, height = image_to_rgba_bytes(image)
    fps = max(1, int(fps))

    appsrc = Gst.ElementFactory.make('appsrc', f'{layer_key}_appsrc')
    convert = Gst.ElementFactory.make('videoconvert', f'{layer_key}_convert')
    if not all([appsrc, convert]):
        raise RuntimeError(f'Failed to create live still chain for {layer_key}')

    caps = Gst.Caps.from_string(
        f'video/x-raw,format=RGBA,width={width},height={height},framerate={fps}/1'
    )
    appsrc.set_property('caps', caps)
    appsrc.set_property('format', Gst.Format.TIME)
    appsrc.set_property('is-live', True)
    appsrc.set_property('do-timestamp', True)
    # Never block the pusher thread on a stalled mixer — that deadlocks
    # force-live aggregation. Bound queued bytes to a couple of frames.
    appsrc.set_property('block', False)
    appsrc.set_property('max-bytes', max(1, len(rgba) * 2))

    return [appsrc, convert], convert, appsrc, rgba, width, height


def start_still_pusher(
    branch: GraphicBranch,
    *,
    max_frames: int | None = None,
) -> None:
    """
    Feed still RGBA into appsrc.

    For backgrounds under a force-live compositor, pass max_frames (one-shot).
    Pair with compositor pad max-last-buffer-repeat=CLOCK_TIME_NONE so the
    mixer keeps reusing the last frame without a continuous RGBA push that
    starves x264/RTMP.
    """
    stop_still_pusher(branch)
    if (
        branch.appsrc is None
        or not branch.still_rgba
        or branch.still_width <= 0
        or branch.still_height <= 0
    ):
        return

    stop = threading.Event()
    appsrc = branch.appsrc
    rgba = branch.still_rgba
    fps = max(1, int(branch.still_fps))
    frame_duration = Gst.SECOND // fps
    # One-shot: burst a few frames quickly so negotiation + leaky queue fill.
    interval = 0.0 if max_frames is not None else (1.0 / fps)

    def _run() -> None:
        frames = 0
        while not stop.is_set():
            buf = Gst.Buffer.new_allocate(None, len(rgba), None)
            buf.fill(0, rgba)
            # Let appsrc do-timestamp assign running-time PTS (valid 0 would stick).
            buf.pts = Gst.CLOCK_TIME_NONE
            buf.duration = frame_duration
            retval = appsrc.emit('push-buffer', buf)
            if retval != Gst.FlowReturn.OK:
                if not stop.is_set():
                    logger.warning(
                        'still appsrc push-buffer returned %s for %s',
                        retval,
                        branch.layer_key,
                    )
                break
            frames += 1
            if max_frames is not None and frames >= max_frames:
                break
            if interval <= 0:
                continue
            if stop.wait(interval):
                break
        # Do not emit EOS — that races teardown and can end the live pipeline.

    thread = threading.Thread(
        target=_run,
        name=f'graphic-still-{branch.layer_key}',
        daemon=True,
    )
    branch._stop_push = stop
    branch._push_thread = thread
    thread.start()
    if max_frames is not None:
        # Ensure frames land before attach returns (mixer can latch last buffer).
        thread.join(timeout=2.0)


def stop_still_pusher(branch: GraphicBranch) -> None:
    if branch._stop_push is not None:
        branch._stop_push.set()
    if branch._push_thread is not None:
        branch._push_thread.join(timeout=1.0)
    branch._stop_push = None
    branch._push_thread = None


def build_video_loop_chain(
    *,
    layer_key: str,
    url: str,
    width: int,
    height: int,
    fit: str,
    fps: int = 30,
) -> tuple[list[Gst.Element], Gst.Element, list[Any]]:
    """
    uridecodebin → videoconvert → videoscale → capsfilter.

    Dynamic pads are handled by the caller via signal_handlers.
    Caller attaches a paced graphics ingest tail.
    """
    decode = Gst.ElementFactory.make('uridecodebin', f'{layer_key}_decode')
    convert = Gst.ElementFactory.make('videoconvert', f'{layer_key}_vconvert')
    scale = Gst.ElementFactory.make('videoscale', f'{layer_key}_vscale')
    capsfilter = Gst.ElementFactory.make('capsfilter', f'{layer_key}_vcaps')
    if not all([decode, convert, scale, capsfilter]):
        raise RuntimeError(f'Failed to create video background chain for {layer_key}')

    decode.set_property('uri', url)
    scale.set_property('add-borders', fit != 'stretch')
    capsfilter.set_property(
        'caps',
        Gst.Caps.from_string(
            f'video/x-raw,width={width},height={height},framerate={max(1, int(fps))}/1'
        ),
    )

    static = [convert, scale, capsfilter]
    return [decode, *static], capsfilter, []


def download_and_prepare_still(
    url: str,
    *,
    max_w: int | None = None,
    max_h: int | None = None,
) -> Image.Image:
    image = load_image_from_url(url)
    if max_w and max_h:
        w, h = scale_to_box(image.width, image.height, max_w, max_h)
        image = image.resize((w, h), Image.Resampling.LANCZOS)
    return image


def still_from_config_url(config: dict[str, Any]) -> Image.Image:
    url = resolve_url(config)
    if not url:
        raise ValueError('Graphic config has no url/source')
    return load_image_from_url(url)


def rendered_rgba_signature_parts(image: Image.Image) -> dict[str, Any]:
    raw, w, h = image_to_rgba_bytes(image)
    digest = hashlib.sha256(raw).hexdigest()
    return {'w': w, 'h': h, 'digest': digest}
