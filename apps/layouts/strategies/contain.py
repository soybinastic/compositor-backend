"""CONTAIN layout — equal grid tiles with aspect-ratio preservation."""

from __future__ import annotations

import math

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig
from apps.layouts.types import LayoutType


class ContainLayout(LayoutStrategy):
    layout_type = LayoutType.CONTAIN

    def compute_tiles(
        self,
        source_ids: list[str],
        canvas: Size,
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        if not source_ids:
            return []

        count = len(source_ids)
        columns = math.ceil(math.sqrt(count))
        rows = math.ceil(count / columns)
        tile_width = canvas.width // columns
        tile_height = canvas.height // rows

        tiles: list[TileConfig] = []
        for index, source_id in enumerate(source_ids):
            row = index // columns
            column = index % columns
            tiles.append(
                TileConfig(
                    source_id=source_id,
                    x=column * tile_width,
                    y=row * tile_height,
                    width=tile_width,
                    height=tile_height,
                    zorder=1,
                )
            )

        return tiles
