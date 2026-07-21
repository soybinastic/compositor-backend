"""SPOTLIGHT — host large on the left; others stacked in a right strip."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig, resolve_host_id
from apps.layouts.types import LayoutType, ScaleMode


class SpotlightLayout(LayoutStrategy):
    layout_type = LayoutType.SPOTLIGHT

    HOST_WIDTH_RATIO = 0.7

    def compute_tiles(
        self,
        source_ids: list[str],
        canvas: Size,
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        if not source_ids:
            return []

        host_id = resolve_host_id(source_ids, host_source_id)
        others = [source_id for source_id in source_ids if source_id != host_id]

        if not others:
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

        host_width = max(int(canvas.width * self.HOST_WIDTH_RATIO), 1)
        strip_width = canvas.width - host_width
        tiles = [
            TileConfig(
                source_id=host_id,
                x=0,
                y=0,
                width=host_width,
                height=canvas.height,
                zorder=1,
                scale_mode=ScaleMode.CONTAIN,
            )
        ]

        tile_height = max(canvas.height // len(others), 1)
        for index, source_id in enumerate(others):
            tiles.append(
                TileConfig(
                    source_id=source_id,
                    x=host_width,
                    y=index * tile_height,
                    width=strip_width,
                    height=tile_height if index < len(others) - 1 else canvas.height - index * tile_height,
                    zorder=2,
                    scale_mode=ScaleMode.CONTAIN,
                )
            )
        return tiles
