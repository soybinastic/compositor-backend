"""Session graphics state helpers."""

from __future__ import annotations

import copy
from typing import Any

from apps.graphics.constants import ALL_LAYERS


def empty_graphics_state() -> dict[str, Any]:
    return {layer: None for layer in ALL_LAYERS}


def merge_graphics_state(
    current: dict[str, Any] | None,
    partial: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge a partial update into current state.

    Missing keys leave existing layers unchanged. Explicit ``None`` clears a layer.
    """
    merged = empty_graphics_state()
    if current:
        for key in ALL_LAYERS:
            if key in current:
                merged[key] = copy.deepcopy(current[key])

    for key, value in partial.items():
        if key not in ALL_LAYERS:
            continue
        if value is None:
            merged[key] = None
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def snapshot_graphics_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return empty_graphics_state()
    return {layer: copy.deepcopy(state.get(layer)) for layer in ALL_LAYERS}
