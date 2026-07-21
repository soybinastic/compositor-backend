"""Configurable GStreamer video mix backends (CPU / OpenGL / CUDA)."""

from __future__ import annotations

import logging
from typing import Literal, Protocol

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

logger = logging.getLogger(__name__)

VideoBackendName = Literal['cpu', 'gl', 'cuda']
RequestedVideoBackend = Literal['cpu', 'gl', 'cuda', 'auto']

_VALID_REQUESTED = frozenset({'cpu', 'gl', 'cuda', 'auto'})
_VALID_RESOLVED = frozenset({'cpu', 'gl', 'cuda'})

# Elements required for each resolved backend (mixer + memory transfer).
_BACKEND_REQUIRED_ELEMENTS: dict[str, tuple[str, ...]] = {
    'cpu': ('compositor',),
    'gl': ('glcompositor', 'glupload', 'glcolorconvert', 'gldownload'),
    'cuda': ('cudacompositor', 'cudaupload', 'cudadownload'),
}


class VideoMixBackend(Protocol):
    """Builds mixer and per-branch chains for one video memory domain."""

    name: VideoBackendName

    def create_mixer(self, name: str = 'mix') -> Gst.Element:
        ...

    def build_ingest_tail(self, peer_id: str) -> list[Gst.Element]:
        """Elements after decode/videoconvert; last links to the mixer sink."""
        ...

    def build_post_mixer_chain(self, *, width: int, height: int) -> list[Gst.Element]:
        """Elements after mixer; last links to video_tee (system memory)."""
        ...


def element_available(factory_name: str) -> bool:
    return Gst.ElementFactory.find(factory_name) is not None


def probe_backend_elements() -> dict[str, bool]:
    """Report availability of key mixer factories for health checks."""
    return {
        'compositor': element_available('compositor'),
        'glcompositor': element_available('glcompositor'),
        'cudacompositor': element_available('cudacompositor'),
    }


def backend_supported(backend: str) -> bool:
    required = _BACKEND_REQUIRED_ELEMENTS.get(backend)
    if required is None:
        return False
    return all(element_available(name) for name in required)


def resolve_video_backend(
    requested: str,
    *,
    cuda_device_id: int = -1,
) -> VideoBackendName:
    """
    Resolve requested backend to a concrete mixer backend.

    - auto: prefer cuda → gl → cpu (first fully supported)
    - explicit gl/cuda: raise if required elements are missing
    - cpu: always required; raise if compositor is unavailable
    """
    _ = cuda_device_id  # reserved for future device probes
    requested = (requested or 'auto').strip().lower()
    if requested not in _VALID_REQUESTED:
        raise ValueError(
            f'Invalid video backend {requested!r}; '
            "expected 'cpu', 'gl', 'cuda', or 'auto'"
        )

    if requested == 'auto':
        for candidate in ('cuda', 'gl', 'cpu'):
            if backend_supported(candidate):
                if candidate != 'cpu':
                    logger.info(
                        'COMPOSITOR_VIDEO_BACKEND=auto resolved to %s',
                        candidate,
                    )
                else:
                    logger.info(
                        'COMPOSITOR_VIDEO_BACKEND=auto resolved to cpu '
                        '(no GPU mixer plugins available)'
                    )
                return candidate  # type: ignore[return-value]
        raise RuntimeError(
            'No video mix backend available (compositor/glcompositor/cudacompositor)'
        )

    if not backend_supported(requested):
        missing = [
            name
            for name in _BACKEND_REQUIRED_ELEMENTS[requested]
            if not element_available(name)
        ]
        raise RuntimeError(
            f'Video backend {requested!r} requested but missing GStreamer elements: '
            f'{", ".join(missing)}'
        )

    return requested  # type: ignore[return-value]


def get_video_mix_backend(
    resolved: str,
    *,
    cuda_device_id: int = -1,
) -> VideoMixBackend:
    if resolved not in _VALID_RESOLVED:
        raise ValueError(f'Unknown resolved video backend: {resolved!r}')
    if resolved == 'cpu':
        return CpuVideoMixBackend()
    if resolved == 'gl':
        return GlVideoMixBackend()
    return CudaVideoMixBackend(cuda_device_id=cuda_device_id)


def _make_element(factory_name: str, name: str) -> Gst.Element:
    element = Gst.ElementFactory.make(factory_name, name)
    if element is None:
        raise RuntimeError(f'Failed to create GStreamer element {factory_name!r} ({name})')
    return element


def _configure_leaky_queue(queue: Gst.Element) -> None:
    queue.set_property('leaky', 2)
    queue.set_property('max-size-time', 2 * Gst.SECOND)


def _configure_aggregator_mixer(mixer: Gst.Element) -> None:
    """Apply common compositor-like properties when present on the element."""
    if mixer.find_property('background') is not None:
        mixer.set_property('background', 1)  # black
    if mixer.find_property('start-time-selection') is not None:
        mixer.set_property('start-time-selection', 0)  # first
    if mixer.find_property('latency') is not None:
        mixer.set_property('latency', 40 * Gst.MSECOND)
    if mixer.find_property('ignore-inactive-pads') is not None:
        mixer.set_property('ignore-inactive-pads', True)


def _create_force_live_mixer(factory_name: str, name: str) -> Gst.Element:
    factory = Gst.ElementFactory.find(factory_name)
    if factory is None:
        raise RuntimeError(f'GStreamer element factory {factory_name!r} not found')

    # force-live is construct-only on GstAggregator subclasses.
    mixer = factory.create_with_properties(['name', 'force-live'], [name, True])
    if mixer is None:
        mixer = Gst.ElementFactory.make(factory_name, name)
    if mixer is None:
        raise RuntimeError(f'Failed to create mixer element {factory_name!r}')
    _configure_aggregator_mixer(mixer)
    return mixer


class CpuVideoMixBackend:
    name: VideoBackendName = 'cpu'

    def create_mixer(self, name: str = 'mix') -> Gst.Element:
        return _create_force_live_mixer('compositor', name)

    def build_ingest_tail(self, peer_id: str) -> list[Gst.Element]:
        scale = _make_element('videoscale', f'video_scale_{peer_id}')
        queue = _make_element('queue', f'video_queue_{peer_id}')
        scale.set_property('add-borders', True)
        _configure_leaky_queue(queue)
        return [scale, queue]

    def build_post_mixer_chain(self, *, width: int, height: int) -> list[Gst.Element]:
        capsfilter = _make_element('capsfilter', 'out_caps')
        convert = _make_element('videoconvert', 'out_convert')
        capsfilter.set_property(
            'caps',
            Gst.Caps.from_string(f'video/x-raw,width={width},height={height}'),
        )
        return [capsfilter, convert]


class GlVideoMixBackend:
    name: VideoBackendName = 'gl'

    def create_mixer(self, name: str = 'mix') -> Gst.Element:
        return _create_force_live_mixer('glcompositor', name)

    def build_ingest_tail(self, peer_id: str) -> list[Gst.Element]:
        upload = _make_element('glupload', f'gl_upload_{peer_id}')
        color = _make_element('glcolorconvert', f'gl_color_{peer_id}')
        queue = _make_element('queue', f'video_queue_{peer_id}')
        _configure_leaky_queue(queue)
        return [upload, color, queue]

    def build_post_mixer_chain(self, *, width: int, height: int) -> list[Gst.Element]:
        color = _make_element('glcolorconvert', 'out_gl_color')
        download = _make_element('gldownload', 'out_gl_download')
        capsfilter = _make_element('capsfilter', 'out_caps')
        convert = _make_element('videoconvert', 'out_convert')
        capsfilter.set_property(
            'caps',
            Gst.Caps.from_string(f'video/x-raw,width={width},height={height}'),
        )
        return [color, download, capsfilter, convert]


class CudaVideoMixBackend:
    name: VideoBackendName = 'cuda'

    def __init__(self, *, cuda_device_id: int = -1) -> None:
        self._cuda_device_id = cuda_device_id

    def create_mixer(self, name: str = 'mix') -> Gst.Element:
        mixer = _create_force_live_mixer('cudacompositor', name)
        if mixer.find_property('cuda-device-id') is not None:
            mixer.set_property('cuda-device-id', self._cuda_device_id)
        return mixer

    def build_ingest_tail(self, peer_id: str) -> list[Gst.Element]:
        upload = _make_element('cudaupload', f'cuda_upload_{peer_id}')
        queue = _make_element('queue', f'video_queue_{peer_id}')
        if upload.find_property('cuda-device-id') is not None:
            upload.set_property('cuda-device-id', self._cuda_device_id)
        _configure_leaky_queue(queue)
        return [upload, queue]

    def build_post_mixer_chain(self, *, width: int, height: int) -> list[Gst.Element]:
        download = _make_element('cudadownload', 'out_cuda_download')
        capsfilter = _make_element('capsfilter', 'out_caps')
        convert = _make_element('videoconvert', 'out_convert')
        if download.find_property('cuda-device-id') is not None:
            download.set_property('cuda-device-id', self._cuda_device_id)
        capsfilter.set_property(
            'caps',
            Gst.Caps.from_string(f'video/x-raw,width={width},height={height}'),
        )
        return [download, capsfilter, convert]
