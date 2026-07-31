"""GStreamer background music branch mixed into the session audiomixer."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

from apps.scenes.background_music import (
    normalize_background_music_config,
    resolve_background_music_url,
    validate_background_music_track_url,
)

logger = logging.getLogger(__name__)

PLAYBACK_IDLE = 'idle'
PLAYBACK_LOADING = 'loading'
PLAYBACK_READY = 'ready'
PLAYBACK_PLAYING = 'playing'
PLAYBACK_PAUSED = 'paused'
PLAYBACK_STOPPED = 'stopped'
PLAYBACK_BUFFERING = 'buffering'
PLAYBACK_ERROR = 'error'


@dataclass
class BackgroundMusicBranch:
    uridecodebin: Gst.Element
    volume_element: Gst.Element
    audio_queue: Gst.Element
    mixer_sink_pad: Gst.Pad
    elements: list[Gst.Element] = field(default_factory=list)
    signal_handlers: list[tuple[Gst.Element, int]] = field(default_factory=list)
    track_url: str | None = None
    track_title: str | None = None


class BackgroundMusicManager:
    """Decode a scene track URL and feed the shared audiomixer."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._pipeline: Gst.Pipeline | None = None
        self._audiomixer: Gst.Element | None = None
        self._branch: BackgroundMusicBranch | None = None
        self._config: dict[str, Any] = normalize_background_music_config(None)
        self._scene_id: str | None = None
        self._playback_state = PLAYBACK_IDLE
        self._error: dict[str, str] | None = None
        self._duration_ms = 0
        self._bus_handler_id: int | None = None
        self._bus_handler_id_error: int | None = None
        self._lock = threading.RLock()

    def attach(self, pipeline: Gst.Pipeline, audiomixer: Gst.Element) -> None:
        with self._lock:
            self._pipeline = pipeline
            self._audiomixer = audiomixer
            bus = pipeline.get_bus()
            if bus is not None:
                bus.add_signal_watch()
                self._bus_handler_id = bus.connect('message::eos', self._on_bus_eos)
                self._bus_handler_id_error = bus.connect('message::error', self._on_bus_error)

    def detach(self) -> None:
        with self._lock:
            self._teardown_branch_unlocked()
            if self._pipeline is not None and self._bus_handler_id is not None:
                bus = self._pipeline.get_bus()
                if bus is not None:
                    bus.remove_signal_watch()
                    try:
                        bus.disconnect(self._bus_handler_id)
                    except Exception:
                        pass
                    try:
                        bus.disconnect(self._bus_handler_id_error)
                    except Exception:
                        pass
                self._bus_handler_id = None
                self._bus_handler_id_error = None
            self._pipeline = None
            self._audiomixer = None
            self._playback_state = PLAYBACK_IDLE

    def apply_config(self, config: dict[str, Any] | None, *, scene_id: str | None = None) -> None:
        with self._lock:
            self._scene_id = scene_id
            self._config = normalize_background_music_config(config)
            url = resolve_background_music_url(self._config)

            if not url:
                self._teardown_branch_unlocked()
                self._playback_state = PLAYBACK_IDLE
                self._error = None
                self._duration_ms = 0
                return

            if self._branch is not None and self._branch.track_url == url:
                self._apply_volume_properties_unlocked()
                if self._playback_state == PLAYBACK_IDLE:
                    self._playback_state = PLAYBACK_READY
                return

            self._teardown_branch_unlocked()
            self._playback_state = PLAYBACK_LOADING
            self._error = None
            try:
                validate_background_music_track_url(url)
                track = self._config.get('track') or {}
                self._build_branch_unlocked(
                    url=url,
                    title=str(track.get('title') or ''),
                )
                self._apply_volume_properties_unlocked()
                self._playback_state = PLAYBACK_READY
            except Exception as exc:
                logger.exception(
                    'Failed to load background music for session %s',
                    self.session_id,
                )
                self._teardown_branch_unlocked()
                self._playback_state = PLAYBACK_ERROR
                self._error = {
                    'code': 'decode_failed',
                    'message': str(exc),
                }

    def play(self) -> None:
        with self._lock:
            if self._branch is None:
                raise ValueError('no_track_loaded')
            self._playback_state = PLAYBACK_LOADING
            self._branch.uridecodebin.set_state(Gst.State.PLAYING)
            for element in self._branch.elements:
                element.sync_state_with_parent()
            self._refresh_duration_unlocked()
            self._playback_state = PLAYBACK_PLAYING

    def pause(self) -> None:
        with self._lock:
            if self._branch is None:
                raise ValueError('no_track_loaded')
            self._branch.uridecodebin.set_state(Gst.State.PAUSED)
            self._playback_state = PLAYBACK_PAUSED

    def resume(self) -> None:
        with self._lock:
            if self._branch is None:
                raise ValueError('no_track_loaded')
            self._branch.uridecodebin.set_state(Gst.State.PLAYING)
            self._playback_state = PLAYBACK_PLAYING

    def stop(self) -> None:
        with self._lock:
            if self._branch is None:
                raise ValueError('no_track_loaded')
            self._seek_to_start_unlocked()
            self._branch.uridecodebin.set_state(Gst.State.READY)
            self._playback_state = PLAYBACK_STOPPED

    def set_volume(self, volume: float, *, muted: bool | None = None) -> None:
        with self._lock:
            self._config['volume'] = max(0.0, min(1.0, float(volume)))
            if muted is not None:
                self._config['muted'] = bool(muted)
            self._apply_volume_properties_unlocked()

    def get_runtime_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                'scene_id': self._scene_id,
                'playback_state': self._playback_state,
                'position_ms': self._query_position_ms_unlocked(),
                'duration_ms': self._duration_ms,
                'error': self._error,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }

    def _build_branch_unlocked(self, *, url: str, title: str) -> None:
        assert self._pipeline is not None
        assert self._audiomixer is not None

        src = Gst.ElementFactory.make('uridecodebin', 'bgm_src')
        audio_convert = Gst.ElementFactory.make('audioconvert', 'bgm_convert')
        audio_resample = Gst.ElementFactory.make('audioresample', 'bgm_resample')
        volume_element = Gst.ElementFactory.make('volume', 'bgm_volume')
        audio_queue = Gst.ElementFactory.make('queue', 'bgm_queue')

        if not all([src, audio_convert, audio_resample, volume_element, audio_queue]):
            raise RuntimeError('Failed to create background music elements')

        src.set_property('uri', url)
        audio_queue.set_property('leaky', 2)
        audio_queue.set_property('max-size-time', 2 * Gst.SECOND)

        elements = [src, audio_convert, audio_resample, volume_element, audio_queue]
        if not audio_convert.link(audio_resample):
            raise RuntimeError('Failed to link background music audioconvert -> audioresample')
        if not audio_resample.link(volume_element):
            raise RuntimeError('Failed to link background music audioresample -> volume')
        if not volume_element.link(audio_queue):
            raise RuntimeError('Failed to link background music volume -> queue')

        mixer_pad = self._audiomixer.get_request_pad('sink_%u')
        if mixer_pad is None:
            raise RuntimeError('Failed to request audiomixer sink pad for background music')

        for element in elements:
            self._pipeline.add(element)

        branch = BackgroundMusicBranch(
            uridecodebin=src,
            volume_element=volume_element,
            audio_queue=audio_queue,
            mixer_sink_pad=mixer_pad,
            elements=elements,
            track_url=url,
            track_title=title or None,
        )

        def on_pad_added(_element: Gst.Element, pad: Gst.Pad, _user_data) -> None:
            caps = pad.get_current_caps() or pad.query_caps(None)
            if caps is None:
                return
            structure = caps.get_structure(0)
            if structure is None or not structure.get_name().startswith('audio/'):
                return

            sink_pad = audio_convert.get_static_pad('sink')
            if sink_pad is None or sink_pad.is_linked():
                return
            if pad.link(sink_pad) != Gst.PadLinkReturn.OK:
                raise RuntimeError('Failed to link background music decode pad')

            audio_src_pad = audio_queue.get_static_pad('src')
            if audio_src_pad is None or audio_src_pad.is_linked():
                return
            if audio_src_pad.link(mixer_pad) != Gst.PadLinkReturn.OK:
                raise RuntimeError('Failed to link background music branch to audiomixer')

        handler_id = src.connect('pad-added', on_pad_added, None)
        branch.signal_handlers.append((src, handler_id))

        for element in elements:
            element.sync_state_with_parent()

        self._branch = branch

    def _teardown_branch_unlocked(self) -> None:
        branch = self._branch
        if branch is None:
            return

        for element, handler_id in branch.signal_handlers:
            try:
                element.disconnect(handler_id)
            except Exception:
                pass

        if self._audiomixer is not None and branch.mixer_sink_pad is not None:
            try:
                self._audiomixer.release_request_pad(branch.mixer_sink_pad)
            except Exception:
                pass

        if self._pipeline is not None:
            for element in branch.elements:
                try:
                    element.set_state(Gst.State.NULL)
                except Exception:
                    pass
                try:
                    self._pipeline.remove(element)
                except Exception:
                    pass

        self._branch = None
        self._duration_ms = 0

    def _apply_volume_properties_unlocked(self) -> None:
        if self._branch is None:
            return
        level = 0.0 if self._config.get('muted') else float(self._config.get('volume', 0.5))
        self._branch.volume_element.set_property('volume', level)

    def _seek_to_start_unlocked(self) -> None:
        if self._branch is None:
            return
        ok = self._branch.uridecodebin.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            0,
        )
        if not ok:
            logger.debug(
                'Background music seek-to-start failed for session %s',
                self.session_id,
            )

    def _query_position_ms_unlocked(self) -> int:
        if self._branch is None:
            return 0
        ok, position = self._branch.uridecodebin.query_position(Gst.Format.TIME)
        if not ok:
            return 0
        return max(0, int(position // Gst.MSECOND))

    def _refresh_duration_unlocked(self) -> None:
        if self._branch is None:
            self._duration_ms = 0
            return
        ok, duration = self._branch.uridecodebin.query_duration(Gst.Format.TIME)
        if ok and duration > 0:
            self._duration_ms = int(duration // Gst.MSECOND)

    def _on_bus_eos(self, bus: Gst.Bus, message: Gst.Message) -> None:
        src = message.src
        with self._lock:
            if self._branch is None or src != self._branch.uridecodebin:
                return
            if self._config.get('loop'):
                self._seek_to_start_unlocked()
                self._branch.uridecodebin.set_state(Gst.State.PLAYING)
                self._playback_state = PLAYBACK_PLAYING
                return
            self._playback_state = PLAYBACK_STOPPED

    def _on_bus_error(self, bus: Gst.Bus, message: Gst.Message) -> None:
        src = message.src
        with self._lock:
            if self._branch is None or src != self._branch.uridecodebin:
                return
            _gerror, debug = message.parse_error()
            logger.error(
                'Background music error for session %s: %s (%s)',
                self.session_id,
                _gerror,
                debug,
            )
            self._playback_state = PLAYBACK_ERROR
            self._error = {
                'code': 'decode_failed',
                'message': str(_gerror.message if _gerror else 'Background music decode failed'),
            }
