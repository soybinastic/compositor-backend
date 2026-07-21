"""GRID layout — fixed 2×2 (≤4 sources) or 3×3 (≤9 sources)."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig
from apps.layouts.types import LayoutType, ScaleMode


class GridLayout(LayoutStrategy):
    layout_type = LayoutType.GRID

    def compute_tiles(
        self,
        source_ids: list[str],
        canvas: Size,
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        _ = host_source_id
        if not source_ids:
            return []

        count = len(source_ids)
        if count <= 4:
            columns, rows = 2, 2
        else:
            columns, rows = 3, 3

        # Cap visible sources to the fixed grid capacity.
        visible = source_ids[: columns * rows]
        tile_width = canvas.width // columns
        tile_height = canvas.height // rows

        tiles: list[TileConfig] = []
        for index, source_id in enumerate(visible):
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
                    scale_mode=ScaleMode.CONTAIN,
                )
            )
        return tiles
