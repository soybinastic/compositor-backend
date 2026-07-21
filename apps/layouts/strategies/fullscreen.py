"""FULLSCREEN — only the host (or first source) fills the canvas."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig, resolve_host_id
from apps.layouts.types import LayoutType, ScaleMode


class FullscreenLayout(LayoutStrategy):
    layout_type = LayoutType.FULLSCREEN

    def compute_tiles(
        self,
        source_ids: list[str],
        canvas: Size,
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        if not source_ids:
            return []

        host_id = resolve_host_id(source_ids, host_source_id)
        return [
            TileConfig(
                source_id=host_id,
                x=0,
                y=0,
                width=canvas.width,
                height=canvas.height,
                zorder=1,
                scale_mode=ScaleMode.CONTAIN,
            )
        ]
