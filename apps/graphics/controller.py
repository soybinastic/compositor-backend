"""Apply / remove graphics layers on a running CompositorPipeline."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

from apps.graphics.constants import (
    LAYER_BACKGROUND,
    LAYER_BANNER,
    LAYER_CHAT,
    LAYER_COUNTDOWN,
    LAYER_LOGO,
    LAYER_OVERLAY,
    LAYER_QR,
    LAYER_TICKER,
    LAYER_TICKER_BACKGROUND,
    LOGO_MAX_HEIGHT,
    LOGO_MAX_WIDTH,
)
from apps.graphics.geometry import (
    banner_bottom_inset,
    banner_layout,
    chat_geometry,
    countdown_geometry,
    logo_geometry,
    overlay_geometry,
    qr_geometry,
    ticker_geometry,
)
from apps.graphics.gst_branches import (
    GraphicBranch,
    content_signature,
    download_and_prepare_still,
    is_video_url,
    still_from_config_url,
    stop_still_pusher,
)
from apps.graphics.post_mixer_overlays import (
    LAYER_GRAPHICS_STACK,
    PixbufLayerState,
    STATIC_STACK_ORDER,
    apply_pixbuf_to_overlay,
    clear_pixbuf_overlay,
    compose_static_stack,
)
from apps.graphics.renderers.pil_overlays import (
    banner_bar_height,
    banner_content_width,
    render_banner_bar,
    render_chat_panel,
    render_countdown_overlay,
    render_ticker_background,
    render_ticker_text_strip,
    ticker_bar_height,
)
from apps.graphics.visibility import (
    background_should_show,
    banner_should_show,
    banner_text_parts,
    chat_should_show,
    logo_should_show,
    overlay_should_show,
    qr_should_show,
    resolve_url,
    ticker_should_show,
    ticker_text,
)

if TYPE_CHECKING:
    from apps.compositor.compositor_pipeline import CompositorPipeline

logger = logging.getLogger(__name__)

_DIRECT_OVERLAY_LAYERS = frozenset(
    {
        LAYER_TICKER,
        LAYER_TICKER_BACKGROUND,
        LAYER_COUNTDOWN,
    }
)


class GraphicsController:
    """Owns graphic mixer pads for one CompositorPipeline (caller holds pipeline lock)."""

    def __init__(self, owner: CompositorPipeline) -> None:
        self._owner = owner
        # Legacy compositor-pad branches (unused for backgrounds; kept for teardown).
        self._branches: dict[str, GraphicBranch] = {}
        # Top layers + background use post-mixer gdkpixbufoverlay (no force-live pads).
        self._pixbuf_layers: dict[str, PixbufLayerState] = {}
        self._video_cutouts: list[tuple[int, int, int, int]] = []
        self._ticker_stop = threading.Event()
        self._ticker_thread: threading.Thread | None = None
        self._countdown_stop = threading.Event()
        self._countdown_thread: threading.Thread | None = None
        self._countdown_started_at = 0.0
        self._countdown_duration = 0
        self._pending_state: dict[str, Any] = {}
        self._stack_fingerprint: object | None = None

    @property
    def branches(self) -> dict[str, GraphicBranch]:
        return self._branches

    @property
    def background_active(self) -> bool:
        state = self._pixbuf_layers.get(LAYER_BACKGROUND)
        return bool(state and state.visible and state._image is not None)

    def stop(self) -> None:
        """Full teardown (pipeline shutdown)."""
        self._stop_ticker_animation()
        self._stop_countdown_animation()
        for key in list(self._branches.keys()):
            self._remove_branch(key)
        for key in list(self._pixbuf_layers.keys()):
            self._clear_pixbuf_layer(key)

    def clear_live(self) -> None:
        """Clear live graphics (post-mixer overlays). Keeps config persistence separate."""
        self._stop_ticker_animation()
        self._stop_countdown_animation()
        for key in list(self._pixbuf_layers.keys()):
            self._clear_pixbuf_layer(key)
        self._video_cutouts = []
        self._pending_state = {}
        self._stack_fingerprint = None

    def set_pending_state(self, state: dict[str, Any]) -> None:
        """Refresh persisted graphics config without applying layers."""
        self._pending_state = state

    def set_video_cutouts(
        self,
        cutouts: list[tuple[int, int, int, int]],
        *,
        rebuild: bool = True,
    ) -> None:
        """Camera tile rects — background is drawn only outside these areas."""
        self._video_cutouts = list(cutouts)
        if rebuild and self.background_active:
            self._rebuild_graphics_stack()

    def commit_layout_overlays(self, cutouts: list[tuple[int, int, int, int]]) -> None:
        """Single stack rebuild after tiles + background visibility are settled."""
        self._video_cutouts = list(cutouts)
        self._rebuild_graphics_stack()

    def apply_state(
        self,
        state: dict[str, Any],
        *,
        layout: str,
        layout_only: bool = False,
        prepared_background=None,
    ) -> None:
        self._pending_state = state
        if layout_only:
            self.sync_background_visibility(
                layout,
                prepared_image=prepared_background,
                rebuild=False,
            )
            # Ticker Y may shift when chat presence changes, but chat itself is unchanged.
            self._reposition_ticker_if_present(layout)
            self._reposition_banner_if_present()
            return

        self._apply_background(
            state.get(LAYER_BACKGROUND),
            layout,
            prepared_image=prepared_background,
        )
        self._apply_overlay(state.get(LAYER_OVERLAY))
        self._apply_logo(state.get(LAYER_LOGO))
        self._apply_qr(state.get(LAYER_QR))
        self._apply_banner(state.get(LAYER_BANNER))
        self._apply_ticker(state.get(LAYER_TICKER), chat_active=chat_should_show(state.get(LAYER_CHAT)))
        self._apply_chat(state.get(LAYER_CHAT))

    def prefetch_background_still(self, state: dict[str, Any], layout: str):
        """
        Download + cover-resize background outside the pipeline lock.

        Returns a PIL image, or None when background should not load.
        """
        config = state.get(LAYER_BACKGROUND) if state else None
        if not background_should_show(config, layout):
            return None
        assert config is not None
        url = resolve_url(config)
        if not url or is_video_url(url):
            return None
        fit = str(config.get('fit') or 'cover')
        sig = content_signature({'url': url, 'fit': fit, 'layer': LAYER_BACKGROUND})
        existing = self._pixbuf_layers.get(LAYER_BACKGROUND)
        if existing and existing.signature == sig and existing._image is not None:
            return existing._image
        return self._load_background_image(url, fit)

    def sync_background_visibility(
        self,
        layout: str,
        *,
        prepared_image=None,
        rebuild: bool = True,
    ) -> None:
        config = self._pending_state.get(LAYER_BACKGROUND)
        if background_should_show(config, layout):
            existing = self._pixbuf_layers.get(LAYER_BACKGROUND)
            if existing and existing._image is not None:
                existing.visible = True
                if rebuild:
                    self._rebuild_graphics_stack()
            elif config:
                self._apply_background(
                    config,
                    layout,
                    prepared_image=prepared_image,
                    rebuild=rebuild,
                )
            return
        self._hide_background(rebuild=rebuild)

    def _hide_background(self, *, rebuild: bool = True) -> None:
        """Hide background but keep decoded image so layout flips are cheap."""
        existing = self._pixbuf_layers.get(LAYER_BACKGROUND)
        if existing is None:
            return
        if not existing.visible and existing._image is not None:
            return
        existing.visible = False
        if rebuild:
            self._rebuild_graphics_stack()

    def _reposition_ticker_if_present(self, _layout: str) -> None:
        text_state = self._pixbuf_layers.get(LAYER_TICKER)
        config = self._pending_state.get(LAYER_TICKER)
        if text_state is None or not config or not text_state.visible:
            return
        chat_active = chat_should_show(self._pending_state.get(LAYER_CHAT))
        bar_h = text_state.geometry[3]
        bar_geom = ticker_geometry(
            self._owner.width,
            self._owner.height,
            position=str(config.get('tickerPosition') or 'bottom'),
            bar_height=bar_h,
            chat_active=chat_active,
        )
        bg_state = self._pixbuf_layers.get(LAYER_TICKER_BACKGROUND)
        if bg_state is not None:
            bg_state.geometry = bar_geom
            bg_element = self._owner._post_mixer_overlays.get(LAYER_TICKER_BACKGROUND)
            if bg_element is not None:
                bg_element.set_property('offset-x', int(bar_geom[0]))
                bg_element.set_property('offset-y', int(bar_geom[1]))

        strip_w = text_state.geometry[2]
        text_geom = (text_state.geometry[0], bar_geom[1], strip_w, bar_h)
        text_state.geometry = text_geom
        text_element = self._owner._post_mixer_overlays.get(LAYER_TICKER)
        if text_element is not None:
            text_element.set_property('offset-y', int(bar_geom[1]))
        self._reposition_banner_if_present()

    def _resolve_banner_bottom_inset(self) -> int:
        ticker_config = self._pending_state.get(LAYER_TICKER)
        chat_active = chat_should_show(self._pending_state.get(LAYER_CHAT))
        bottom_ticker_active = bool(
            ticker_should_show(ticker_config)
            and str((ticker_config or {}).get('tickerPosition') or 'bottom').lower() != 'top'
        )
        return banner_bottom_inset(
            bottom_ticker_active=bottom_ticker_active,
            chat_active=chat_active,
            ticker_bar_height=ticker_bar_height(),
        )

    def _banner_layout_geometries(
        self,
        config: dict[str, Any],
    ) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int] | None]:
        title, description = banner_text_parts(config)
        font_size = int(config.get('font_size') or 36)
        canvas_w = self._owner.width
        canvas_h = self._owner.height
        bar_width = banner_content_width(
            title,
            description,
            font_size,
            canvas_w=canvas_w,
        )
        primary_height = banner_bar_height(font_size)
        desc_font_size = max(16, font_size - 8)
        secondary_height = banner_bar_height(desc_font_size) if description else 0
        return banner_layout(
            canvas_w,
            canvas_h,
            bar_width=bar_width,
            primary_height=primary_height,
            secondary_height=secondary_height,
            font_size=font_size,
            has_secondary=bool(description),
            bottom_inset=self._resolve_banner_bottom_inset(),
        )

    def _reposition_banner_if_present(self) -> None:
        config = self._pending_state.get(LAYER_BANNER)
        if not banner_should_show(config):
            return
        assert config is not None
        primary_geom, secondary_geom = self._banner_layout_geometries(config)
        changed = False
        primary_state = self._pixbuf_layers.get('banner_primary')
        if primary_state is not None and primary_state.visible:
            primary_state.geometry = primary_geom
            changed = True
        if secondary_geom is not None:
            secondary_state = self._pixbuf_layers.get('banner_secondary')
            if secondary_state is not None and secondary_state.visible:
                secondary_state.geometry = secondary_geom
                changed = True
        if changed:
            self._rebuild_graphics_stack()

    # --- per-layer apply -------------------------------------------------

    def _load_background_image(self, url: str, fit: str):
        from PIL import Image

        image = download_and_prepare_still(
            url,
            max_w=self._owner.width,
            max_h=self._owner.height,
        )
        if fit == 'stretch':
            return image.resize(
                (self._owner.width, self._owner.height),
                Image.Resampling.LANCZOS,
            )
        return _cover_resize(image, self._owner.width, self._owner.height)

    def _apply_background(
        self,
        config: dict[str, Any] | None,
        layout: str,
        *,
        prepared_image=None,
        rebuild: bool = True,
    ) -> None:
        """
        Draw background via post-mixer graphics_stack (same RTMP-safe path as logo).

        Cameras are inset so a margin remains; the stack draws background only
        outside those tiles so live video shows through.
        """
        if not background_should_show(config, layout):
            self._hide_background(rebuild=rebuild)
            return
        assert config is not None
        url = resolve_url(config)
        assert url is not None
        if is_video_url(url):
            logger.warning(
                'Video backgrounds are not supported on the live mix (session %s); '
                'use a still image',
                self._owner.session_id,
            )
            self._hide_background(rebuild=rebuild)
            return

        fit = str(config.get('fit') or 'cover')
        sig = content_signature({'url': url, 'fit': fit, 'layer': LAYER_BACKGROUND})
        existing = self._pixbuf_layers.get(LAYER_BACKGROUND)
        if existing and existing.signature == sig and existing._image is not None:
            existing.visible = True
            if rebuild:
                self._rebuild_graphics_stack()
            return

        image = prepared_image
        if image is None:
            logger.warning(
                'Background download running under pipeline lock (session %s); '
                'prefer prefetch_background_still',
                self._owner.session_id,
            )
            image = self._load_background_image(url, fit)
        geom = (0, 0, self._owner.width, self._owner.height)
        self._set_pixbuf_layer(
            LAYER_BACKGROUND,
            image,
            geometry=geom,
            signature=sig,
            rebuild=rebuild,
        )
        logger.info(
            'Applied post-mixer background for session %s (%sx%s, layout=%s)',
            self._owner.session_id,
            self._owner.width,
            self._owner.height,
            layout,
        )

    def _apply_overlay(self, config: dict[str, Any] | None) -> None:
        if not overlay_should_show(config):
            self._clear_pixbuf_layer(LAYER_OVERLAY)
            return
        assert config is not None
        url = resolve_url(config)
        assert url is not None
        geom = overlay_geometry(self._owner.width, self._owner.height, config)
        sig = content_signature({'url': url, 'geom': geom, 'layer': LAYER_OVERLAY})
        existing = self._pixbuf_layers.get(LAYER_OVERLAY)
        if existing and existing.signature == sig and existing.visible:
            self._show_pixbuf_layer(LAYER_OVERLAY, geom)
            return
        image = still_from_config_url(config).resize((geom[2], geom[3]))
        self._set_pixbuf_layer(LAYER_OVERLAY, image, geometry=geom, signature=sig)

    def _apply_logo(self, config: dict[str, Any] | None) -> None:
        if not logo_should_show(config):
            self._clear_pixbuf_layer(LAYER_LOGO)
            return
        assert config is not None
        url = resolve_url(config)
        assert url is not None
        placement = (
            config.get('placement')
            or config.get('logoPosition')
            or config.get('position')
        )
        pre_sig = content_signature(
            {
                'url': url,
                'placement': placement,
                'layer': LAYER_LOGO,
            }
        )
        existing = self._pixbuf_layers.get(LAYER_LOGO)
        if existing and existing.signature.startswith(pre_sig) and existing.visible:
            self._show_pixbuf_layer(LAYER_LOGO, existing.geometry)
            return

        image = download_and_prepare_still(
            url,
            max_w=LOGO_MAX_WIDTH,
            max_h=LOGO_MAX_HEIGHT,
        )
        geom = logo_geometry(
            self._owner.width,
            self._owner.height,
            image.width,
            image.height,
            config,
        )
        sig = pre_sig + content_signature({'geom': geom})
        image = image.resize((geom[2], geom[3]))
        self._set_pixbuf_layer(LAYER_LOGO, image, geometry=geom, signature=sig)

    def _apply_qr(self, config: dict[str, Any] | None) -> None:
        if not qr_should_show(config):
            self._clear_pixbuf_layer(LAYER_QR)
            return
        assert config is not None
        url = resolve_url(config)
        assert url is not None
        geom = qr_geometry(self._owner.width, self._owner.height, config)
        sig = content_signature(
            {
                'url': url,
                'geom': geom,
                'title': config.get('title'),
                'primary': config.get('primary'),
                'secondary': config.get('secondary'),
                'font': config.get('font'),
                'layer': LAYER_QR,
            }
        )
        existing = self._pixbuf_layers.get(LAYER_QR)
        if existing and existing.signature == sig and existing.visible:
            self._show_pixbuf_layer(LAYER_QR, geom)
            return
        image = still_from_config_url(config).resize((geom[2], geom[3]))
        self._set_pixbuf_layer(LAYER_QR, image, geometry=geom, signature=sig)

    def _apply_banner(self, config: dict[str, Any] | None) -> None:
        if not banner_should_show(config):
            self._clear_pixbuf_layer('banner_primary')
            self._clear_pixbuf_layer('banner_secondary')
            return
        assert config is not None
        title, description = banner_text_parts(config)
        font_size = int(config.get('font_size') or 36)
        theme = str(config.get('theme') or 'plain')
        primary = str(config.get('primary') or '')
        secondary = str(config.get('secondary') or '')
        accent = str(config.get('accent') or '')
        bottom_inset = self._resolve_banner_bottom_inset()
        sig = content_signature(
            {
                'title': title,
                'description': description,
                'font_size': font_size,
                'theme': theme,
                'primary': primary,
                'secondary': secondary,
                'accent': accent,
                'bottom_inset': bottom_inset,
                'layer': LAYER_BANNER,
            }
        )
        existing = self._pixbuf_layers.get('banner_primary')
        if existing and existing.signature == sig and existing.visible:
            return

        self._clear_pixbuf_layer('banner_primary')
        self._clear_pixbuf_layer('banner_secondary')

        primary_geom, secondary_geom = self._banner_layout_geometries(config)
        bar_width = primary_geom[2]
        desc_font_size = max(16, font_size - 8)

        if title:
            img = render_banner_bar(
                width=bar_width,
                title=title,
                theme=theme,
                primary=primary,
                secondary=secondary,
                accent=accent,
                font_size=font_size,
                is_primary=True,
            )
            self._set_pixbuf_layer(
                'banner_primary',
                img,
                geometry=primary_geom,
                signature=sig,
            )

        if description and secondary_geom is not None:
            img = render_banner_bar(
                width=bar_width,
                title=description,
                theme=theme,
                primary=primary,
                secondary=secondary,
                accent=accent,
                font_size=desc_font_size,
                is_primary=False,
            )
            self._set_pixbuf_layer(
                'banner_secondary',
                img,
                geometry=secondary_geom,
                signature=sig + ':sec',
            )

    def _apply_ticker(self, config: dict[str, Any] | None, *, chat_active: bool) -> None:
        if not ticker_should_show(config):
            self._clear_ticker_layers()
            self._reposition_banner_if_present()
            return
        assert config is not None
        text = ticker_text(config)
        direction = str(config.get('tickerDirection') or 'rtl')
        speed = float(config.get('tickerSpeed') or 2.0)
        position = str(config.get('tickerPosition') or 'bottom')
        primary = str(config.get('primary') or '')
        secondary = str(config.get('secondary') or '')
        style = config.get('bannerTickerStyle') if isinstance(config.get('bannerTickerStyle'), dict) else {}
        if style:
            primary = str(style.get('primary') or primary)
            secondary = str(style.get('secondary') or secondary)
        sig = content_signature(
            {
                'text': text,
                'direction': direction,
                'speed': speed,
                'position': position,
                'primary': primary,
                'secondary': secondary,
                'chat_active': chat_active,
                'layer': LAYER_TICKER,
            }
        )
        existing = self._pixbuf_layers.get(LAYER_TICKER)
        if existing and existing.signature == sig and existing.visible:
            self._reposition_banner_if_present()
            return

        canvas_w = self._owner.width
        canvas_h = self._owner.height
        bg_img = render_ticker_background(
            canvas_width=canvas_w,
            primary=primary,
        )
        text_img = render_ticker_text_strip(
            canvas_width=canvas_w,
            text=text,
            secondary=secondary,
        )
        bar_geom = ticker_geometry(
            canvas_w,
            canvas_h,
            position=position,
            bar_height=bg_img.height,
            chat_active=chat_active,
        )
        self._set_pixbuf_layer(
            LAYER_TICKER_BACKGROUND,
            bg_img,
            geometry=bar_geom,
            signature=sig,
        )
        text_geom = (0, bar_geom[1], text_img.width, text_img.height)
        state = self._set_pixbuf_layer(
            LAYER_TICKER,
            text_img,
            geometry=text_geom,
            signature=sig,
        )
        state.ticker_width = text_img.width
        state.ticker_direction = direction
        state.ticker_speed = speed
        self._start_ticker_animation(state)
        self._reposition_banner_if_present()

    def _clear_ticker_layers(self) -> None:
        self._stop_ticker_animation()
        self._clear_pixbuf_layer(LAYER_TICKER)
        self._clear_pixbuf_layer(LAYER_TICKER_BACKGROUND)

    def _apply_chat(self, config: dict[str, Any] | None) -> None:
        if not chat_should_show(config):
            self._clear_pixbuf_layer(LAYER_CHAT)
            # Reposition ticker if chat turned off.
            self._reposition_ticker_if_present('')
            self._reposition_banner_if_present()
            return
        assert config is not None
        messages = config.get('messages') or []
        sig = content_signature({'enabled': True, 'messages': messages, 'layer': LAYER_CHAT})
        existing = self._pixbuf_layers.get(LAYER_CHAT)
        if existing and existing.signature == sig and existing.visible:
            return
        geom = chat_geometry(self._owner.width, self._owner.height)
        img = render_chat_panel(width=geom[2], height=geom[3], messages=list(messages))
        self._set_pixbuf_layer(LAYER_CHAT, img, geometry=geom, signature=sig)
        self._reposition_ticker_if_present('')
        self._reposition_banner_if_present()

    def start_countdown(self, *, started_at_epoch: float, duration_seconds: int) -> None:
        self._countdown_started_at = started_at_epoch
        self._countdown_duration = max(1, int(duration_seconds))
        self._stop_countdown_animation()
        self._countdown_stop.clear()
        self._render_countdown_frame()
        self._start_countdown_animation()

    def stop_countdown(self) -> None:
        self._stop_countdown_animation()
        self._clear_pixbuf_layer(LAYER_COUNTDOWN)

    def _countdown_seconds_remaining(self) -> int:
        elapsed = time.time() - self._countdown_started_at
        return max(0, int(self._countdown_duration - elapsed + 0.999))

    def _render_countdown_frame(self) -> None:
        remaining = self._countdown_seconds_remaining()
        img = render_countdown_overlay(
            canvas_width=self._owner.width,
            canvas_height=self._owner.height,
            seconds_remaining=remaining,
        )
        geom = countdown_geometry(
            self._owner.width,
            self._owner.height,
            box_width=img.width,
            box_height=img.height,
        )
        self._set_pixbuf_layer(
            LAYER_COUNTDOWN,
            img,
            geometry=geom,
            signature=f'countdown:{remaining}',
        )

    # --- attach helpers --------------------------------------------------

    def _set_pixbuf_layer(
        self,
        layer_key: str,
        image,
        *,
        geometry: tuple[int, int, int, int],
        signature: str,
        rebuild: bool = True,
    ) -> PixbufLayerState:
        state = self._pixbuf_layers.get(layer_key) or PixbufLayerState(layer_key=layer_key)
        state.signature = signature
        state.geometry = geometry
        state.visible = True
        state._image = image.convert('RGBA') if hasattr(image, 'convert') else image
        self._pixbuf_layers[layer_key] = state

        if layer_key in _DIRECT_OVERLAY_LAYERS:
            element = self._owner._post_mixer_overlays.get(layer_key)
            if element is None:
                raise RuntimeError(f'Post-mixer overlay element missing for {layer_key}')
            apply_pixbuf_to_overlay(
                element,
                state._image,
                geometry,
                state=state,
                preserve_image_size=(layer_key == LAYER_TICKER),
            )
            return state

        if rebuild:
            self._rebuild_graphics_stack()
        else:
            self._stack_fingerprint = None
        return state

    def _show_pixbuf_layer(
        self,
        layer_key: str,
        geometry: tuple[int, int, int, int],
    ) -> None:
        state = self._pixbuf_layers.get(layer_key)
        if state is None or state._image is None:
            return
        state.geometry = geometry
        state.visible = True
        if layer_key in _DIRECT_OVERLAY_LAYERS:
            element = self._owner._post_mixer_overlays.get(layer_key)
            if element is None:
                return
            apply_pixbuf_to_overlay(
                element,
                state._image,
                geometry,
                state=state,
                preserve_image_size=(layer_key == LAYER_TICKER),
            )
            return
        self._rebuild_graphics_stack()

    def _clear_pixbuf_layer(self, layer_key: str) -> None:
        state = self._pixbuf_layers.pop(layer_key, None)
        if layer_key in _DIRECT_OVERLAY_LAYERS:
            element = self._owner._post_mixer_overlays.get(layer_key)
            if element is not None:
                clear_pixbuf_overlay(
                    element,
                    state or PixbufLayerState(layer_key=layer_key),
                )
            return
        if state is not None or layer_key in STATIC_STACK_ORDER:
            self._rebuild_graphics_stack()

    def _stack_content_fingerprint(self) -> object:
        layer_parts = []
        for key in STATIC_STACK_ORDER:
            state = self._pixbuf_layers.get(key)
            if state is None or not state.visible or state._image is None:
                layer_parts.append((key, False, '', None))
            else:
                layer_parts.append((key, True, state.signature, state.geometry))
        return (tuple(layer_parts), tuple(self._video_cutouts))

    def _rebuild_graphics_stack(self) -> None:
        element = self._owner._post_mixer_overlays.get(LAYER_GRAPHICS_STACK)
        if element is None:
            return
        fingerprint = self._stack_content_fingerprint()
        if fingerprint == self._stack_fingerprint:
            return
        stack_state = self._pixbuf_layers.get(LAYER_GRAPHICS_STACK) or PixbufLayerState(
            layer_key=LAYER_GRAPHICS_STACK
        )
        composed = compose_static_stack(
            self._pixbuf_layers,
            canvas_w=self._owner.width,
            canvas_h=self._owner.height,
            video_cutouts=self._video_cutouts,
        )
        if composed is None:
            clear_pixbuf_overlay(element, stack_state)
            self._pixbuf_layers.pop(LAYER_GRAPHICS_STACK, None)
            self._stack_fingerprint = fingerprint
            return
        geom = (0, 0, self._owner.width, self._owner.height)
        apply_pixbuf_to_overlay(element, composed, geom, state=stack_state)
        stack_state.signature = 'composed'
        self._pixbuf_layers[LAYER_GRAPHICS_STACK] = stack_state
        self._stack_fingerprint = fingerprint

    def _remove_branch(self, key: str) -> None:
        branch = self._branches.pop(key, None)
        if branch is not None:
            self._teardown_branch(branch)

    def _teardown_branch(self, branch: GraphicBranch) -> None:
        stop_still_pusher(branch)
        owner = self._owner
        handler = getattr(branch, '_signal_handler', None)
        if handler is not None:
            decode, handler_id = handler
            try:
                decode.disconnect(handler_id)
            except Exception:
                pass

        for element in reversed(branch.elements):
            element.set_state(Gst.State.NULL)
            if owner._pipeline is not None:
                owner._pipeline.remove(element)

        if owner._compositor is not None:
            try:
                owner._compositor.release_request_pad(branch.compositor_sink_pad)
            except Exception:
                logger.debug('Failed to release graphic pad for %s', branch.layer_key)

        for path in branch.temp_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _start_ticker_animation(self, state: PixbufLayerState) -> None:
        self._stop_ticker_animation()
        self._ticker_stop.clear()
        owner = self._owner
        fps = max(1, owner.fps)
        element = owner._post_mixer_overlays.get(LAYER_TICKER)
        if element is None:
            return

        def _run() -> None:
            canvas_w = owner.width
            strip_w = state.ticker_width or canvas_w
            direction = state.ticker_direction
            speed = max(0.1, state.ticker_speed)
            ticker_y = state.geometry[1]
            # pixels per frame
            step = max(1, int(speed * 2))
            if direction == 'rtl':
                xpos = canvas_w
            else:
                xpos = -strip_w

            while not self._ticker_stop.is_set():
                if direction == 'rtl':
                    xpos -= step
                    if xpos < -strip_w:
                        xpos = canvas_w
                else:
                    xpos += step
                    if xpos > canvas_w:
                        xpos = -strip_w
                try:
                    element.set_property('offset-x', int(xpos))
                    element.set_property('offset-y', int(ticker_y))
                except Exception:
                    break
                time.sleep(1.0 / fps)

        self._ticker_thread = threading.Thread(
            target=_run,
            name=f'ticker-{owner.session_id[:8]}',
            daemon=True,
        )
        self._ticker_thread.start()

    def _stop_ticker_animation(self) -> None:
        self._ticker_stop.set()
        if self._ticker_thread is not None:
            self._ticker_thread.join(timeout=1.0)
            self._ticker_thread = None

    def _start_countdown_animation(self) -> None:
        self._countdown_stop.clear()
        owner = self._owner

        def _run() -> None:
            while not self._countdown_stop.is_set():
                if self._countdown_seconds_remaining() <= 0:
                    self._render_countdown_frame()
                    break
                self._render_countdown_frame()
                if self._countdown_stop.wait(1.0):
                    break

        self._countdown_thread = threading.Thread(
            target=_run,
            name=f'countdown-{owner.session_id[:8]}',
            daemon=True,
        )
        self._countdown_thread.start()

    def _stop_countdown_animation(self) -> None:
        self._countdown_stop.set()
        if self._countdown_thread is not None:
            self._countdown_thread.join(timeout=1.0)
            self._countdown_thread = None


def _cover_resize(image, width: int, height: int):
    from PIL import Image

    src_w, src_h = image.size
    scale = max(width / src_w, height / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = max(0, (new_w - width) // 2)
    top = max(0, (new_h - height) // 2)
    return resized.crop((left, top, left + width, top + height))
