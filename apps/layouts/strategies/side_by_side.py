"""SIDE_BY_SIDE / HALFSCREEN — 50/50 horizontal split (first two sources)."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig
from apps.layouts.types import LayoutType, ScaleMode


class SideBySideLayout(LayoutStrategy):
    layout_type = LayoutType.SIDE_BY_SIDE

    def compute_tiles(
        self,
        source_ids: list[str],
        canvas: Size,
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        _ = host_source_id
        if not source_ids:
            return []

        if len(source_ids) == 1:
            return [
                TileConfig(
                    source_id=source_ids[0],
                    x=0,
                    y=0,
                    width=canvas.width,
                    height=canvas.height,
                    zorder=1,
                    scale_mode=ScaleMode.CONTAIN,
                )
            ]

        half_width = canvas.width // 2
        left_id, right_id = source_ids[0], source_ids[1]
        return [
            TileConfig(
                source_id=left_id,
                x=0,
                y=0,
                width=half_width,
                height=canvas.height,
                zorder=1,
                scale_mode=ScaleMode.CONTAIN,
            ),
            TileConfig(
                source_id=right_id,
                x=half_width,
                y=0,
                width=canvas.width - half_width,
                height=canvas.height,
                zorder=1,
                scale_mode=ScaleMode.CONTAIN,
            ),
        ]
