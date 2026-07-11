"""THUMBNAIL layout — host main view with smaller participant tiles."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig
from apps.layouts.types import LayoutType


class ThumbnailLayout(LayoutStrategy):
    layout_type = LayoutType.THUMBNAIL

    THUMBNAIL_HEIGHT_RATIO = 0.2

    def compute_tiles(
        self,
        source_ids: list[str],
        canvas: Size,
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        if not source_ids:
            return []

        host_id = host_source_id if host_source_id in source_ids else source_ids[0]
        others = [source_id for source_id in source_ids if source_id != host_id]

        thumbnail_height = max(int(canvas.height * self.THUMBNAIL_HEIGHT_RATIO), 1)
        main_height = canvas.height - thumbnail_height

        tiles = [
            TileConfig(
                source_id=host_id,
                x=0,
                y=0,
                width=canvas.width,
                height=main_height,
                zorder=1,
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
                )
            )

        return tiles
