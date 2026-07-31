"""THUMBNAIL layout — slot 0 main view with smaller tiles along the bottom."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig, split_primary_and_others
from apps.layouts.types import LayoutType, ScaleMode


class ThumbnailLayout(LayoutStrategy):
    layout_type = LayoutType.THUMBNAIL

    THUMBNAIL_HEIGHT_RATIO = 0.2

    def compute_tiles(
        self,
        source_ids: list[str],
        canvas: Size,
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        _ = host_source_id
        if not source_ids:
            return []

        primary_id, others = split_primary_and_others(source_ids)

        thumbnail_height = max(int(canvas.height * self.THUMBNAIL_HEIGHT_RATIO), 1)
        main_height = canvas.height - thumbnail_height

        tiles = [
            TileConfig(
                source_id=primary_id,
                x=0,
                y=0,
                width=canvas.width,
                height=main_height if others else canvas.height,
                zorder=1,
                scale_mode=ScaleMode.CONTAIN,
            )
        ]

        if not others:
            return tiles

        thumbnail_width = max(canvas.width // len(others), 1)
        for index, source_id in enumerate(others):
            tiles.append(
                TileConfig(
                    source_id=source_id,
                    x=index * thumbnail_width,
                    y=main_height,
                    width=thumbnail_width,
                    height=thumbnail_height,
                    zorder=2,
                    scale_mode=ScaleMode.CONTAIN,
                )
            )

        return tiles
