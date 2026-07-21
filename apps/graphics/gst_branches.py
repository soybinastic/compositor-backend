"""GStreamer branch builders for graphics layers."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import tempfile
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
    appsrc: Gst.Element | None = None
    temp_paths: list[Path] = field(default_factory=list)
    geometry: tuple[int, int, int, int] = (0, 0, 1, 1)
    zorder: int = 0
    visible: bool = True
    ticker_width: int = 0
    ticker_direction: str = 'rtl'
    ticker_speed: float = 2.0


def content_signature(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def is_video_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in VIDEO_EXTENSIONS)


def load_image_from_url(url: str) -> Image.Image:
    data = get_asset_cache().fetch(url)
    return Image.open(io.BytesIO(data)).convert('RGBA')


def write_temp_png(image: Image.Image) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix='gst-graphic-', suffix='.png', delete=False)
    path = Path(handle.name)
    handle.close()
    image.save(path, format='PNG')
    return path


def build_still_chain_from_image(
    *,
    layer_key: str,
    image: Image.Image,
    target_w: int | None = None,
    target_h: int | None = None,
) -> tuple[list[Gst.Element], Gst.Element, list[Path]]:
    """
    filesrc → pngdec → imagefreeze → videoconvert → (optional videoscale).

    Returns (elements, last_element, temp_paths).
    """
    if target_w and target_h:
        image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
    path = write_temp_png(image)
    filesrc = Gst.ElementFactory.make('filesrc', f'{layer_key}_filesrc')
    pngdec = Gst.ElementFactory.make('pngdec', f'{layer_key}_pngdec')
    freeze = Gst.ElementFactory.make('imagefreeze', f'{layer_key}_freeze')
    convert = Gst.ElementFactory.make('videoconvert', f'{layer_key}_convert')
    if not all([filesrc, pngdec, freeze, convert]):
        path.unlink(missing_ok=True)
        raise RuntimeError(f'Failed to create still chain for {layer_key}')
    filesrc.set_property('location', str(path))
    elements = [filesrc, pngdec, freeze, convert]
    return elements, convert, [path]


def build_still_chain_from_rgba_appsrc(
    *,
    layer_key: str,
    rgba: bytes,
    width: int,
    height: int,
    fps: int,
) -> tuple[list[Gst.Element], Gst.Element, Gst.Element]:
    """appsrc (one-shot) → imagefreeze → videoconvert."""
    appsrc = Gst.ElementFactory.make('appsrc', f'{layer_key}_appsrc')
    freeze = Gst.ElementFactory.make('imagefreeze', f'{layer_key}_freeze')
    convert = Gst.ElementFactory.make('videoconvert', f'{layer_key}_convert')
    if not all([appsrc, freeze, convert]):
        raise RuntimeError(f'Failed to create appsrc still chain for {layer_key}')

    caps = Gst.Caps.from_string(
        f'video/x-raw,format=RGBA,width={width},height={height},framerate={fps}/1'
    )
    appsrc.set_property('caps', caps)
    appsrc.set_property('format', Gst.Format.TIME)
    appsrc.set_property('is-live', True)
    appsrc.set_property('do-timestamp', True)
    appsrc.set_property('block', False)
    appsrc.set_property('max-bytes', 0)

    return [appsrc, freeze, convert], convert, appsrc


def push_rgba_buffer(appsrc: Gst.Element, rgba: bytes, width: int, height: int) -> None:
    buf = Gst.Buffer.new_allocate(None, len(rgba), None)
    buf.fill(0, rgba)
    buf.pts = 0
    buf.duration = Gst.SECOND
    retval = appsrc.emit('push-buffer', buf)
    if retval != Gst.FlowReturn.OK:
        logger.warning('appsrc push-buffer returned %s for %s', retval, appsrc.get_name())


def build_video_loop_chain(
    *,
    layer_key: str,
    url: str,
    width: int,
    height: int,
    fit: str,
) -> tuple[list[Gst.Element], Gst.Element, list[Any]]:
    """
    uridecodebin → videoconvert → videoscale → capsfilter.

    Dynamic pads are handled by the caller via signal_handlers.
    """
    decode = Gst.ElementFactory.make('uridecodebin', f'{layer_key}_decode')
    convert = Gst.ElementFactory.make('videoconvert', f'{layer_key}_vconvert')
    scale = Gst.ElementFactory.make('videoscale', f'{layer_key}_vscale')
    capsfilter = Gst.ElementFactory.make('capsfilter', f'{layer_key}_vcaps')
    queue = Gst.ElementFactory.make('queue', f'{layer_key}_vqueue')
    if not all([decode, convert, scale, capsfilter, queue]):
        raise RuntimeError(f'Failed to create video background chain for {layer_key}')

    decode.set_property('uri', url)
    scale.set_property('add-borders', fit != 'stretch')
    capsfilter.set_property(
        'caps',
        Gst.Caps.from_string(f'video/x-raw,width={width},height={height}'),
    )
    queue.set_property('leaky', 2)
    queue.set_property('max-size-time', 2 * Gst.SECOND)

    # decode is linked dynamically; static chain starts at convert.
    static = [convert, scale, capsfilter, queue]
    return [decode, *static], queue, []


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
