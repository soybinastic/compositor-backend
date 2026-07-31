"""CINEMA — slot 0 large (~75%); others in a bottom filmstrip with gaps."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig, split_primary_and_others
from apps.layouts.types import LayoutType, ScaleMode


class CinemaLayout(LayoutStrategy):
    layout_type = LayoutType.CINEMA

    HOST_HEIGHT_RATIO = 0.75
    GAP = 16

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

        primary_height = max(int(canvas.height * self.HOST_HEIGHT_RATIO), 1)
        strip_height = canvas.height - primary_height

        tiles = [
            TileConfig(
                source_id=primary_id,
                x=0,
                y=0,
                width=canvas.width,
                height=primary_height if others else canvas.height,
                zorder=1,
                scale_mode=ScaleMode.CONTAIN,
            )
        ]

        if not others:
            return tiles

        gap = self.GAP
        usable_width = max(canvas.width - gap * (len(others) + 1), len(others))
        thumb_width = max(usable_width // len(others), 1)
        thumb_height = max(strip_height - gap * 2, 1)
        y = primary_height + gap

        for index, source_id in enumerate(others):
            tiles.append(
                TileConfig(
                    source_id=source_id,
                    x=gap + index * (thumb_width + gap),
                    y=y,
                    width=thumb_width,
                    height=thumb_height,
                    zorder=2,
                    scale_mode=ScaleMode.CONTAIN,
                )
            )
        return tiles
