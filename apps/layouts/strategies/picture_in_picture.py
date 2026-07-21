"""PICTURE_IN_PICTURE / OVERLAY — host full frame; guests as floating corner tiles."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig, resolve_host_id
from apps.layouts.types import LayoutType, ScaleMode


class PictureInPictureLayout(LayoutStrategy):
    layout_type = LayoutType.PICTURE_IN_PICTURE

    PIP_WIDTH_RATIO = 1 / 6
    PIP_HEIGHT_RATIO = 1 / 6
    MARGIN = 24

    # Corner order: bottom-right, bottom-left, top-right, top-left, then stack upward/left.
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

        tiles = [
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

        if not others:
            return tiles

        pip_width = max(int(canvas.width * self.PIP_WIDTH_RATIO), 1)
        pip_height = max(int(canvas.height * self.PIP_HEIGHT_RATIO), 1)
        margin = self.MARGIN

        corners = [
            (canvas.width - margin - pip_width, canvas.height - margin - pip_height),
            (margin, canvas.height - margin - pip_height),
            (canvas.width - margin - pip_width, margin),
            (margin, margin),
        ]

        for index, source_id in enumerate(others):
            if index < len(corners):
                x, y = corners[index]
            else:
                # Extra guests stack above the bottom-right stack.
                stack = index - len(corners) + 1
                x = canvas.width - margin - pip_width
                y = max(
                    canvas.height - margin - pip_height - stack * (pip_height + margin),
                    margin,
                )

            tiles.append(
                TileConfig(
                    source_id=source_id,
                    x=x,
                    y=y,
                    width=pip_width,
                    height=pip_height,
                    zorder=2 + index,
                    scale_mode=ScaleMode.CONTAIN,
                )
            )
        return tiles
