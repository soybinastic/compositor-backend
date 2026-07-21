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
    LAYER_LOGO,
    LAYER_OVERLAY,
    LAYER_QR,
    LAYER_TICKER,
    LOGO_MAX_HEIGHT,
    LOGO_MAX_WIDTH,
    ZORDER_BACKGROUND,
    ZORDER_BANNER_PRIMARY,
    ZORDER_BANNER_SECONDARY,
    ZORDER_CHAT,
    ZORDER_LOGO,
    ZORDER_OVERLAY,
    ZORDER_QR,
    ZORDER_TICKER,
)
from apps.graphics.geometry import (
    banner_geometry,
    chat_geometry,
    logo_geometry,
    overlay_geometry,
    qr_geometry,
    ticker_geometry,
)
from apps.graphics.gst_branches import (
    GraphicBranch,
    build_still_chain_from_image,
    build_still_chain_from_rgba_appsrc,
    build_video_loop_chain,
    content_signature,
    download_and_prepare_still,
    is_video_url,
    push_rgba_buffer,
    still_from_config_url,
)
from apps.graphics.renderers.pil_overlays import (
    image_to_rgba_bytes,
    render_banner_bar,
    render_chat_panel,
    render_ticker_bar,
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


class GraphicsController:
    """Owns graphic mixer pads for one CompositorPipeline (caller holds pipeline lock)."""

    def __init__(self, owner: CompositorPipeline) -> None:
        self._owner = owner
        self._branches: dict[str, GraphicBranch] = {}
        self._banner_secondary: GraphicBranch | None = None
        self._ticker_stop = threading.Event()
        self._ticker_thread: threading.Thread | None = None
        self._pending_state: dict[str, Any] = {}

    @property
    def branches(self) -> dict[str, GraphicBranch]:
        return self._branches

    def stop(self) -> None:
        self._stop_ticker_animation()
        for key in list(self._branches.keys()):
            self._remove_branch(key)
        if self._banner_secondary is not None:
            self._teardown_branch(self._banner_secondary)
            self._banner_secondary = None

    def apply_state(
        self,
        state: dict[str, Any],
        *,
        layout: str,
        layout_only: bool = False,
    ) -> None:
        self._pending_state = state
        if layout_only:
            self.sync_background_visibility(layout)
            # Ticker Y may shift when chat presence changes, but chat itself is unchanged.
            self._reposition_ticker_if_present(layout)
            return

        self._apply_background(state.get(LAYER_BACKGROUND), layout)
        self._apply_overlay(state.get(LAYER_OVERLAY))
        self._apply_logo(state.get(LAYER_LOGO))
        self._apply_qr(state.get(LAYER_QR))
        self._apply_banner(state.get(LAYER_BANNER))
        self._apply_ticker(state.get(LAYER_TICKER), chat_active=chat_should_show(state.get(LAYER_CHAT)))
        self._apply_chat(state.get(LAYER_CHAT))

    def sync_background_visibility(self, layout: str) -> None:
        config = self._pending_state.get(LAYER_BACKGROUND)
        branch = self._branches.get(LAYER_BACKGROUND)
        should = background_should_show(config, layout)
        if branch is None:
            if should and config:
                self._apply_background(config, layout)
            return
        if should:
            self._show_branch(branch)
        else:
            self._owner._hide_pad(branch.compositor_sink_pad)
            branch.visible = False

    def _reposition_ticker_if_present(self, _layout: str) -> None:
        branch = self._branches.get(LAYER_TICKER)
        config = self._pending_state.get(LAYER_TICKER)
        if branch is None or not config:
            return
        chat_active = chat_should_show(self._pending_state.get(LAYER_CHAT))
        bar_h = branch.geometry[3]
        geom = ticker_geometry(
            self._owner.width,
            self._owner.height,
            position=str(config.get('tickerPosition') or 'bottom'),
            bar_height=bar_h,
            chat_active=chat_active,
        )
        self._set_geometry(branch, geom, ZORDER_TICKER, visible=True)

    # --- per-layer apply -------------------------------------------------

    def _apply_background(self, config: dict[str, Any] | None, layout: str) -> None:
        if not background_should_show(config, layout):
            self._remove_branch(LAYER_BACKGROUND)
            return
        assert config is not None
        url = resolve_url(config)
        assert url is not None
        fit = str(config.get('fit') or 'cover')
        sig = content_signature({'url': url, 'fit': fit, 'layer': LAYER_BACKGROUND})
        existing = self._branches.get(LAYER_BACKGROUND)
        if existing and existing.signature == sig:
            self._show_branch(existing)
            return

        if is_video_url(url):
            branch = self._attach_video_background(url, fit, sig)
        else:
            from PIL import Image

            image = download_and_prepare_still(
                url,
                max_w=self._owner.width,
                max_h=self._owner.height,
            )
            if fit == 'stretch':
                image = image.resize(
                    (self._owner.width, self._owner.height),
                    Image.Resampling.LANCZOS,
                )
            else:
                # cover: scale then crop center
                image = _cover_resize(image, self._owner.width, self._owner.height)
            branch = self._attach_still_image(
                LAYER_BACKGROUND,
                image,
                geometry=(0, 0, self._owner.width, self._owner.height),
                zorder=ZORDER_BACKGROUND,
                signature=sig,
            )
        self._replace_branch(LAYER_BACKGROUND, branch)

    def _apply_overlay(self, config: dict[str, Any] | None) -> None:
        if not overlay_should_show(config):
            self._remove_branch(LAYER_OVERLAY)
            return
        assert config is not None
        url = resolve_url(config)
        assert url is not None
        geom = overlay_geometry(self._owner.width, self._owner.height, config)
        sig = content_signature({'url': url, 'geom': geom, 'layer': LAYER_OVERLAY})
        existing = self._branches.get(LAYER_OVERLAY)
        if existing and existing.signature == sig:
            self._set_geometry(existing, geom, ZORDER_OVERLAY, visible=True)
            return
        image = still_from_config_url(config).resize((geom[2], geom[3]))
        branch = self._attach_still_image(
            LAYER_OVERLAY,
            image,
            geometry=geom,
            zorder=ZORDER_OVERLAY,
            signature=sig,
        )
        self._replace_branch(LAYER_OVERLAY, branch)

    def _apply_logo(self, config: dict[str, Any] | None) -> None:
        if not logo_should_show(config):
            self._remove_branch(LAYER_LOGO)
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
        existing = self._branches.get(LAYER_LOGO)
        if existing and existing.signature.startswith(pre_sig):
            # Same asset + placement: only refresh pad geometry from cached size.
            self._set_geometry(existing, existing.geometry, ZORDER_LOGO, visible=True)
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
        branch = self._attach_still_image(
            LAYER_LOGO,
            image,
            geometry=geom,
            zorder=ZORDER_LOGO,
            signature=sig,
        )
        self._replace_branch(LAYER_LOGO, branch)

    def _apply_qr(self, config: dict[str, Any] | None) -> None:
        if not qr_should_show(config):
            self._remove_branch(LAYER_QR)
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
        existing = self._branches.get(LAYER_QR)
        if existing and existing.signature == sig:
            self._set_geometry(existing, geom, ZORDER_QR, visible=True)
            return
        image = still_from_config_url(config).resize((geom[2], geom[3]))
        branch = self._attach_still_image(
            LAYER_QR,
            image,
            geometry=geom,
            zorder=ZORDER_QR,
            signature=sig,
        )
        self._replace_branch(LAYER_QR, branch)

    def _apply_banner(self, config: dict[str, Any] | None) -> None:
        if not banner_should_show(config):
            self._remove_branch(LAYER_BANNER)
            if self._banner_secondary is not None:
                self._teardown_branch(self._banner_secondary)
                self._banner_secondary = None
            return
        assert config is not None
        title, description = banner_text_parts(config)
        font_size = int(config.get('font_size') or 36)
        theme = str(config.get('theme') or 'plain')
        primary = str(config.get('primary') or '')
        secondary = str(config.get('secondary') or '')
        sig = content_signature(
            {
                'title': title,
                'description': description,
                'font_size': font_size,
                'theme': theme,
                'primary': primary,
                'secondary': secondary,
                'layer': LAYER_BANNER,
            }
        )
        existing = self._branches.get(LAYER_BANNER)
        if existing and existing.signature == sig:
            return

        self._remove_branch(LAYER_BANNER)
        if self._banner_secondary is not None:
            self._teardown_branch(self._banner_secondary)
            self._banner_secondary = None

        canvas_w = self._owner.width
        canvas_h = self._owner.height
        if title:
            img = render_banner_bar(
                width=max(1, canvas_w - 80),
                title=title,
                theme=theme,
                primary=primary,
                secondary=secondary,
                font_size=font_size,
                is_primary=True,
            )
            geom = banner_geometry(
                canvas_w,
                canvas_h,
                primary=True,
                font_size=font_size,
                bar_height=img.height,
            )
            branch = self._attach_rgba_image(
                f'{LAYER_BANNER}_primary',
                img,
                geometry=geom,
                zorder=ZORDER_BANNER_PRIMARY,
                signature=sig,
            )
            self._branches[LAYER_BANNER] = branch

        if description:
            img = render_banner_bar(
                width=max(1, canvas_w - 80),
                title=description,
                theme=theme,
                primary=primary,
                secondary=secondary,
                font_size=max(16, font_size - 8),
                is_primary=False,
            )
            geom = banner_geometry(
                canvas_w,
                canvas_h,
                primary=False,
                font_size=font_size,
                bar_height=img.height,
            )
            self._banner_secondary = self._attach_rgba_image(
                f'{LAYER_BANNER}_secondary',
                img,
                geometry=geom,
                zorder=ZORDER_BANNER_SECONDARY,
                signature=sig + ':sec',
            )

    def _apply_ticker(self, config: dict[str, Any] | None, *, chat_active: bool) -> None:
        if not ticker_should_show(config):
            self._stop_ticker_animation()
            self._remove_branch(LAYER_TICKER)
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
        existing = self._branches.get(LAYER_TICKER)
        if existing and existing.signature == sig:
            return

        img = render_ticker_bar(
            canvas_width=self._owner.width,
            text=text,
            primary=primary,
            secondary=secondary,
        )
        geom = ticker_geometry(
            self._owner.width,
            self._owner.height,
            position=position,
            bar_height=img.height,
            chat_active=chat_active,
        )
        branch = self._attach_rgba_image(
            LAYER_TICKER,
            img,
            geometry=geom,
            zorder=ZORDER_TICKER,
            signature=sig,
        )
        branch.ticker_width = img.width
        branch.ticker_direction = direction
        branch.ticker_speed = speed
        self._replace_branch(LAYER_TICKER, branch)
        self._start_ticker_animation(branch)

    def _apply_chat(self, config: dict[str, Any] | None) -> None:
        if not chat_should_show(config):
            self._remove_branch(LAYER_CHAT)
            # Reposition ticker if chat turned off.
            self._reposition_ticker_if_present('')
            return
        assert config is not None
        messages = config.get('messages') or []
        sig = content_signature({'enabled': True, 'messages': messages, 'layer': LAYER_CHAT})
        existing = self._branches.get(LAYER_CHAT)
        if existing and existing.signature == sig:
            return
        geom = chat_geometry(self._owner.width, self._owner.height)
        img = render_chat_panel(width=geom[2], height=geom[3], messages=list(messages))
        branch = self._attach_rgba_image(
            LAYER_CHAT,
            img,
            geometry=geom,
            zorder=ZORDER_CHAT,
            signature=sig,
        )
        self._replace_branch(LAYER_CHAT, branch)
        self._reposition_ticker_if_present('')

    # --- attach helpers --------------------------------------------------

    def _attach_still_image(
        self,
        layer_key: str,
        image,
        *,
        geometry: tuple[int, int, int, int],
        zorder: int,
        signature: str,
    ) -> GraphicBranch:
        elements, output, temps = build_still_chain_from_image(
            layer_key=layer_key,
            image=image,
        )
        return self._link_graphic_chain(
            layer_key=layer_key,
            source_elements=elements,
            output=output,
            geometry=geometry,
            zorder=zorder,
            signature=signature,
            temp_paths=temps,
        )

    def _attach_rgba_image(
        self,
        layer_key: str,
        image,
        *,
        geometry: tuple[int, int, int, int],
        zorder: int,
        signature: str,
    ) -> GraphicBranch:
        rgba, w, h = image_to_rgba_bytes(image)
        elements, output, appsrc = build_still_chain_from_rgba_appsrc(
            layer_key=layer_key,
            rgba=rgba,
            width=w,
            height=h,
            fps=self._owner.fps,
        )
        branch = self._link_graphic_chain(
            layer_key=layer_key,
            source_elements=elements,
            output=output,
            geometry=geometry,
            zorder=zorder,
            signature=signature,
            appsrc=appsrc,
        )
        push_rgba_buffer(appsrc, rgba, w, h)
        return branch

    def _attach_video_background(self, url: str, fit: str, signature: str) -> GraphicBranch:
        elements, output, _ = build_video_loop_chain(
            layer_key=LAYER_BACKGROUND,
            url=url,
            width=self._owner.width,
            height=self._owner.height,
            fit=fit,
        )
        decode = elements[0]
        static_chain = elements[1:]
        owner = self._owner
        assert owner._pipeline is not None
        assert owner._compositor is not None
        assert owner._video_mix_backend is not None

        tail = owner._video_mix_backend.build_ingest_tail(LAYER_BACKGROUND)
        all_elements = elements + tail
        for element in all_elements:
            owner._pipeline.add(element)

        owner._link_sequential(static_chain + tail, label='graphic-bg-video')

        sink_pad = owner._compositor.get_request_pad('sink_%u')
        if sink_pad is None:
            raise RuntimeError('Failed to request compositor sink pad for background video')

        def _on_pad_added(_decode: Gst.Element, pad: Gst.Pad) -> None:
            caps = pad.get_current_caps() or pad.query_caps(None)
            structure = caps.get_structure(0) if caps and caps.get_size() else None
            if structure is None or not structure.get_name().startswith('video/'):
                return
            sink = static_chain[0].get_static_pad('sink')
            if sink is not None and not sink.is_linked():
                pad.link(sink)

        handler_id = decode.connect('pad-added', _on_pad_added)
        video_src = output.get_static_pad('src') if not tail else tail[-1].get_static_pad('src')
        # Relink: output of static_chain is before tail; last of all_elements after link is tail[-1]
        last = tail[-1]
        src_pad = last.get_static_pad('src')
        if src_pad is None or src_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError('Failed to link background video to compositor')

        for element in all_elements:
            element.sync_state_with_parent()

        branch = GraphicBranch(
            layer_key=LAYER_BACKGROUND,
            compositor_sink_pad=sink_pad,
            elements=all_elements,
            signature=signature,
            geometry=(0, 0, owner.width, owner.height),
            zorder=ZORDER_BACKGROUND,
        )
        # Keep handler alive on branch via elements list; store id on branch for disconnect
        branch._signal_handler = (decode, handler_id)  # type: ignore[attr-defined]
        self._set_geometry(branch, branch.geometry, ZORDER_BACKGROUND, visible=True)
        _ = video_src  # silence lint
        return branch

    def _link_graphic_chain(
        self,
        *,
        layer_key: str,
        source_elements: list[Gst.Element],
        output: Gst.Element,
        geometry: tuple[int, int, int, int],
        zorder: int,
        signature: str,
        temp_paths: list | None = None,
        appsrc: Gst.Element | None = None,
    ) -> GraphicBranch:
        owner = self._owner
        assert owner._pipeline is not None
        assert owner._compositor is not None
        assert owner._video_mix_backend is not None

        tail = owner._video_mix_backend.build_ingest_tail(layer_key)
        all_elements = list(source_elements) + list(tail)
        for element in all_elements:
            owner._pipeline.add(element)

        owner._link_sequential(source_elements, label=f'graphic-{layer_key}-src')
        if not output.link(tail[0]):
            raise RuntimeError(f'Failed to link graphic source to ingest tail for {layer_key}')
        if len(tail) > 1:
            owner._link_sequential(tail, label=f'graphic-{layer_key}-tail')

        sink_pad = owner._compositor.get_request_pad('sink_%u')
        if sink_pad is None:
            raise RuntimeError(f'Failed to request compositor sink pad for {layer_key}')

        src_pad = tail[-1].get_static_pad('src')
        if src_pad is None or src_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError(f'Failed to link graphic branch to compositor for {layer_key}')

        for element in all_elements:
            element.sync_state_with_parent()

        branch = GraphicBranch(
            layer_key=layer_key,
            compositor_sink_pad=sink_pad,
            elements=all_elements,
            signature=signature,
            appsrc=appsrc,
            temp_paths=list(temp_paths or []),
            geometry=geometry,
            zorder=zorder,
        )
        self._set_geometry(branch, geometry, zorder, visible=True)
        return branch

    def _replace_branch(self, key: str, branch: GraphicBranch) -> None:
        old = self._branches.pop(key, None)
        self._branches[key] = branch
        if old is not None:
            self._teardown_branch(old)

    def _remove_branch(self, key: str) -> None:
        branch = self._branches.pop(key, None)
        if branch is not None:
            self._teardown_branch(branch)

    def _teardown_branch(self, branch: GraphicBranch) -> None:
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

    def _set_geometry(
        self,
        branch: GraphicBranch,
        geometry: tuple[int, int, int, int],
        zorder: int,
        *,
        visible: bool,
    ) -> None:
        x, y, w, h = geometry
        pad = branch.compositor_sink_pad
        owner = self._owner
        owner._set_pad_property_if_present(pad, 'xpos', x)
        owner._set_pad_property_if_present(pad, 'ypos', y)
        owner._set_pad_property_if_present(pad, 'width', w)
        owner._set_pad_property_if_present(pad, 'height', h)
        owner._set_pad_property_if_present(pad, 'zorder', zorder)
        owner._set_pad_property_if_present(pad, 'alpha', 1.0 if visible else 0.0)
        branch.geometry = geometry
        branch.zorder = zorder
        branch.visible = visible

    def _show_branch(self, branch: GraphicBranch) -> None:
        self._set_geometry(branch, branch.geometry, branch.zorder, visible=True)

    def _start_ticker_animation(self, branch: GraphicBranch) -> None:
        self._stop_ticker_animation()
        self._ticker_stop.clear()
        owner = self._owner
        fps = max(1, owner.fps)

        def _run() -> None:
            xpos = branch.geometry[0]
            canvas_w = owner.width
            strip_w = branch.ticker_width or canvas_w
            direction = branch.ticker_direction
            speed = max(0.1, branch.ticker_speed)
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
                    owner._set_pad_property_if_present(
                        branch.compositor_sink_pad,
                        'xpos',
                        int(xpos),
                    )
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
