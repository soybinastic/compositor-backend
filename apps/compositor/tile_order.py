"""Resolve compositor video source order from session/scene tile config."""

from __future__ import annotations

from apps.layouts.types import LayoutType

GRID_MAX_VISIBLE = 9


def layout_max_visible(layout: str | None) -> int | None:
    """Return the max number of tiled sources for a layout, or None if uncapped."""
    if layout == LayoutType.GRID.value:
        return GRID_MAX_VISIBLE
    return None


def normalize_slot_assignments(raw: dict | None) -> dict[int, str]:
    """Parse slot assignments from API/DB JSON (string keys → int keys)."""
    if not raw:
        return {}
    normalized: dict[int, str] = {}
    for key, source_id in raw.items():
        if not source_id or not isinstance(source_id, str):
            continue
        try:
            slot = int(key)
        except (TypeError, ValueError):
            continue
        if slot < 0:
            continue
        normalized[slot] = source_id
    return normalized


def has_slot_assignments(assignments: dict | None) -> bool:
    """True when an assignments map contains at least one explicit slot."""
    return bool(normalize_slot_assignments(assignments))


def resolve_effective_assignments(
    session_tile_order_config: dict | None,
    scene_sources_config: dict | None,
) -> dict[int, str] | None:
    """
    Hybrid precedence: scene override when non-empty, else session override,
    else None (use default host-first ordering).
    """
    scene_raw = (scene_sources_config or {}).get('assignments')
    scene = normalize_slot_assignments(scene_raw)
    if scene:
        return scene

    session_raw = (session_tile_order_config or {}).get('assignments')
    session = normalize_slot_assignments(session_raw)
    if session:
        return session

    return None


def default_source_order(
    visible_ids: list[str],
    *,
    host_peer_id: str | None,
    host_owned_source_ids: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Host peer first, then host-owned sources, then remaining peers (stable order)."""
    host_owned = host_owned_source_ids or frozenset()
    ordered: list[str] = []
    used: set[str] = set()

    if host_peer_id and host_peer_id in visible_ids:
        ordered.append(host_peer_id)
        used.add(host_peer_id)

    for source_id in visible_ids:
        if source_id in host_owned and source_id not in used:
            ordered.append(source_id)
            used.add(source_id)

    for source_id in visible_ids:
        if source_id not in used:
            ordered.append(source_id)
            used.add(source_id)

    return ordered


def _apply_slot_assignments(
    visible_ids: list[str],
    slot_assignments: dict[int, str],
    default_order: list[str],
) -> list[str]:
    visible_set = set(visible_ids)
    used: set[str] = set()
    max_slot = max(slot_assignments.keys())
    slots: list[str | None] = [None] * (max_slot + 1)

    for slot in sorted(slot_assignments.keys()):
        source_id = slot_assignments[slot]
        if source_id not in visible_set or source_id in used:
            continue
        slots[slot] = source_id
        used.add(source_id)

    unassigned = [source_id for source_id in default_order if source_id not in used]
    fill_cursor = 0
    for index in range(len(slots)):
        if slots[index] is None and fill_cursor < len(unassigned):
            slots[index] = unassigned[fill_cursor]
            used.add(unassigned[fill_cursor])
            fill_cursor += 1

    ordered = [source_id for source_id in slots if source_id is not None]
    for source_id in unassigned[fill_cursor:]:
        if source_id not in used:
            ordered.append(source_id)
            used.add(source_id)

    return ordered


def resolve_source_order(
    active_source_ids: list[str],
    *,
    host_peer_id: str | None,
    slot_assignments: dict[int, str] | None = None,
    hidden_source_ids: set[str] | frozenset[str] | None = None,
    host_owned_source_ids: set[str] | frozenset[str] | None = None,
    max_visible: int | None = None,
) -> list[str]:
    """
    Return the ordered list of source ids to pass into layout strategies.

    Hidden sources are excluded. Explicit slot assignments take precedence;
    unassigned visible sources fill remaining positions using default ordering.
    When max_visible is set, trailing sources are omitted (hidden overflow).
    """
    hidden = hidden_source_ids or frozenset()
    visible_ids = [source_id for source_id in active_source_ids if source_id not in hidden]

    default_order = default_source_order(
        visible_ids,
        host_peer_id=host_peer_id,
        host_owned_source_ids=host_owned_source_ids,
    )

    if slot_assignments:
        ordered = _apply_slot_assignments(visible_ids, slot_assignments, default_order)
    else:
        ordered = default_order

    if max_visible is not None and max_visible >= 0:
        return ordered[:max_visible]
    return ordered


def sanitize_assignments_for_storage(raw: dict | None) -> dict[str, str]:
    """Normalize slot assignments to string-keyed JSON for DB storage."""
    normalized = normalize_slot_assignments(raw)
    return {str(slot): source_id for slot, source_id in sorted(normalized.items())}


def sanitize_hidden_source_ids(raw: list | None) -> list[str]:
    if not raw:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        source_id = item.strip()
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        result.append(source_id)
    return result


def attached_source_ids_from_scene_items(items: list | None) -> list[str]:
    """Source ids present on a scene (visible or hidden SceneItems)."""
    if not items:
        return []
    attached: list[str] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        source_id = raw.get('sourceId') or raw.get('source_id')
        if not isinstance(source_id, str):
            continue
        source_id = source_id.strip()
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        attached.append(source_id)
    return attached


def hidden_source_ids_from_scene_items(items: list | None) -> list[str]:
    """Source ids with SceneItem.visible == false (program out must hide these)."""
    if not items:
        return []
    hidden: list[str] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if raw.get('visible', True) is not False:
            continue
        source_id = raw.get('sourceId') or raw.get('source_id')
        if not isinstance(source_id, str):
            continue
        source_id = source_id.strip()
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        hidden.append(source_id)
    return hidden


def hidden_session_sources_not_on_scene(
    session_source_ids: list[str] | None,
    items: list | None,
) -> list[str]:
    """
    Session-registry Source ids that are not attached to the active scene.

    Still-producing Sources from another scene must stay off program out when
    the active scene does not list them (produce may continue; mix hides them).
    """
    attached = set(attached_source_ids_from_scene_items(items))
    return [
        source_id
        for source_id in sanitize_hidden_source_ids(session_source_ids)
        if source_id not in attached
    ]


def merge_hidden_source_ids(*groups: list[str] | None) -> list[str]:
    """Union of hidden id lists, stable order (first occurrence wins)."""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for source_id in sanitize_hidden_source_ids(group):
            if source_id in seen:
                continue
            seen.add(source_id)
            merged.append(source_id)
    return merged


def merge_tile_order_config(
    incoming: dict | None,
    existing: dict | None = None,
) -> dict:
    from apps.sessions.constants import DEFAULT_TILE_ORDER_CONFIG

    merged = dict(DEFAULT_TILE_ORDER_CONFIG)
    if existing:
        merged.update(existing)
    if not incoming:
        return merged
    if 'version' in incoming and isinstance(incoming['version'], int):
        merged['version'] = incoming['version']
    if 'assignments' in incoming:
        raw_assignments = incoming.get('assignments')
        if raw_assignments == {}:
            merged['assignments'] = {}
        else:
            combined = sanitize_assignments_for_storage(merged.get('assignments'))
            combined.update(sanitize_assignments_for_storage(raw_assignments))
            merged['assignments'] = combined
    return merged


def merge_sources_config(
    incoming: dict | None,
    existing: dict | None = None,
) -> dict:
    from apps.scenes.constants import DEFAULT_SOURCES_CONFIG

    merged = dict(DEFAULT_SOURCES_CONFIG)
    if existing:
        merged.update(existing)
        if 'assignments' not in merged:
            merged['assignments'] = {}
        if 'items' not in merged:
            merged['items'] = []
    if not incoming:
        return merged
    if 'version' in incoming and isinstance(incoming['version'], int):
        merged['version'] = incoming['version']
    if 'sources' in incoming and isinstance(incoming['sources'], list):
        merged['sources'] = incoming['sources']
    if 'items' in incoming and isinstance(incoming['items'], list):
        merged['items'] = incoming['items']
        if merged.get('version', 1) < 2:
            merged['version'] = 2
        # When items are provided without explicit assignments, derive slots.
        if 'assignments' not in incoming:
            merged['assignments'] = assignments_from_scene_items(incoming['items'])
    if 'assignments' in incoming:
        raw_assignments = incoming.get('assignments')
        if raw_assignments == {}:
            merged['assignments'] = {}
        else:
            combined = sanitize_assignments_for_storage(merged.get('assignments'))
            combined.update(sanitize_assignments_for_storage(raw_assignments))
            merged['assignments'] = combined
    return merged


def assignments_from_scene_items(items: list | None) -> dict[str, str]:
    """Build slot assignments from visible SceneItems ordered by zIndex."""
    if not items:
        return {}
    ordered: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if raw.get('visible', True) is False:
            continue
        source_id = raw.get('sourceId') or raw.get('source_id')
        if not source_id:
            continue
        try:
            z_index = int(raw.get('zIndex', raw.get('z_index', 0)))
        except (TypeError, ValueError):
            z_index = 0
        ordered.append({'sourceId': str(source_id), 'zIndex': z_index})
    ordered.sort(key=lambda item: item['zIndex'])
    return {str(index): item['sourceId'] for index, item in enumerate(ordered)}
