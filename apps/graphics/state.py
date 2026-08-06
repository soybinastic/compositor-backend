"""Session graphics state helpers."""

from __future__ import annotations

import copy
from typing import Any

from apps.graphics.constants import ALL_LAYERS, GRAPHICS_STYLE_KEYS, STYLE_FONTS


def empty_graphics_state() -> dict[str, Any]:
    state: dict[str, Any] = {layer: None for layer in ALL_LAYERS}
    for key in GRAPHICS_STYLE_KEYS:
        state[key] = None
    return state


def merge_graphics_state(
    current: dict[str, Any] | None,
    partial: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge a partial update into current state.

    Missing keys leave existing layers/styles unchanged. Explicit ``None`` clears
    a layer or unsets a style key (e.g. fonts → legacy burn-in).
    """
    merged = empty_graphics_state()
    if current:
        for key in ALL_LAYERS:
            if key in current:
                merged[key] = copy.deepcopy(current[key])
        for key in GRAPHICS_STYLE_KEYS:
            if key in current:
                merged[key] = copy.deepcopy(current[key])
        # Accept legacy alias when reading existing persisted state.
        if STYLE_FONTS not in current and current.get('fontFamily') is not None:
            merged[STYLE_FONTS] = copy.deepcopy(current.get('fontFamily'))

    for key, value in partial.items():
        if key == 'fontFamily':
            key = STYLE_FONTS
        if key in ALL_LAYERS:
            if value is None:
                merged[key] = None
            else:
                merged[key] = copy.deepcopy(value)
        elif key in GRAPHICS_STYLE_KEYS:
            if value is None or value == '':
                merged[key] = None
            else:
                merged[key] = copy.deepcopy(value)
    return merged


def snapshot_graphics_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return empty_graphics_state()
    snapshot = empty_graphics_state()
    for layer in ALL_LAYERS:
        if layer in state:
            snapshot[layer] = copy.deepcopy(state.get(layer))
    for key in GRAPHICS_STYLE_KEYS:
        if key in state:
            snapshot[key] = copy.deepcopy(state.get(key))
    if STYLE_FONTS not in state and state.get('fontFamily') is not None:
        snapshot[STYLE_FONTS] = copy.deepcopy(state.get('fontFamily'))
    return snapshot
