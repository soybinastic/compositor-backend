"""COVER layout — same grid as CONTAIN, but tiles fill (crop/stretch) instead of letterbox."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig
from apps.layouts.strategies.contain import ContainLayout
from apps.layouts.types import LayoutType, ScaleMode


class CoverLayout(LayoutStrategy):
    layout_type = LayoutType.COVER

    def __init__(self) -> None:
        self._contain = ContainLayout()

    def compute_tiles(
        self,
        source_ids: list[str],
        canvas: Size,
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        tiles = self._contain.compute_tiles(
            source_ids,
            canvas,
            host_source_id=host_source_id,
        )
        return [
            TileConfig(
                source_id=tile.source_id,
                x=tile.x,
                y=tile.y,
                width=tile.width,
                height=tile.height,
                zorder=tile.zorder,
                scale_mode=ScaleMode.COVER,
            )
            for tile in tiles
        ]
