"""FULLSCREEN — only slot 0 fills the canvas."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig, split_primary_and_others
from apps.layouts.types import LayoutType, ScaleMode


class FullscreenLayout(LayoutStrategy):
    layout_type = LayoutType.FULLSCREEN

    def compute_tiles(
        self,
        source_ids: list[str],
        canvas: Size,
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        _ = host_source_id
        if not source_ids:
            return []

        primary_id, _others = split_primary_and_others(source_ids)
        return [
            TileConfig(
                source_id=primary_id,
                x=0,
                y=0,
                width=canvas.width,
                height=canvas.height,
                zorder=1,
                scale_mode=ScaleMode.CONTAIN,
            )
        ]
