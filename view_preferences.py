"""Small, local-only view preferences; never part of scientific project data."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re


DEFAULTS = {
    "version": 1,
    "linked_ppm": True,
    "auto_y": True,
    "library_visible": True,
    "table_visible": False,
    "library_fraction": 0.28,
    "graph_percent": 68,
}


def preferences_path() -> Path:
    override = os.environ.get("HIMERA_VIEW_PREFERENCES")
    if override:
        return Path(override)
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config")
    return local / "Himera" / "view_preferences.json"


def normalized_preferences(payload: object) -> dict:
    result = dict(DEFAULTS)
    if not isinstance(payload, dict):
        return result
    for key in ("linked_ppm", "auto_y", "library_visible", "table_visible", "maximized"):
        if isinstance(payload.get(key), bool):
            result[key] = payload[key]
    for key, low, high in (("library_fraction", 0.12, 0.55), ("graph_percent", 30, 85)):
        value = payload.get(key)
        if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
            result[key] = min(high, max(low, value))
    geometry = payload.get("geometry")
    if isinstance(geometry, str) and re.fullmatch(
        r"\d{2,5}x\d{2,5}[+-]\d{1,6}[+-]\d{1,6}", geometry
    ):
        result["geometry"] = geometry
    return result


def read_preferences(path: Path | None = None) -> dict:
    try:
        return normalized_preferences(json.loads((path or preferences_path()).read_text("utf-8")))
    except (OSError, ValueError, TypeError):
        return dict(DEFAULTS)


def write_preferences(payload: dict, path: Path | None = None) -> bool:
    """A preferences failure must never prevent closing or saving spectra."""
    try:
        target = path or preferences_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(normalized_preferences(payload), ensure_ascii=False), "utf-8")
        return True
    except (OSError, ValueError, TypeError):
        return False


def visible_geometry(geometry: str, screen: tuple[int, int], minimum=(950, 620)) -> str:
    """Recover an off-screen window after unplugging/changing the display."""
    match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
    if not match:
        return f"{minimum[0]}x{minimum[1]}+0+0"
    width, height, x, y = map(int, match.groups())
    screen_width, screen_height = max(1, screen[0]), max(1, screen[1])
    width = max(minimum[0], min(width, screen_width))
    height = max(minimum[1], min(height, max(1, screen_height - 40)))
    x = min(max(0, x), max(0, screen_width - width))
    y = min(max(0, y), max(0, screen_height - min(height, 120)))
    return f"{width}x{height}+{x}+{y}"


def usable_limits(limits, domain=None) -> tuple[float, float] | None:
    try:
        left, right = map(float, limits)
        if not math.isfinite(left) or not math.isfinite(right) or left == right:
            return None
        if domain is not None:
            low, high = sorted(map(float, domain))
            if min(left, right) >= high or max(left, right) <= low:
                return None
        return left, right
    except (TypeError, ValueError, OverflowError):
        return None


def capture_table_view(table) -> dict:
    return {
        "selection": tuple(table.selection()),
        "focus": table.focus(),
        "x": table.xview()[0],
        "y": table.yview()[0],
    }


def restore_table_view(table, state: dict) -> None:
    rows = set(table.get_children())
    selection = [iid for iid in state.get("selection", ()) if iid in rows]
    table.selection_set(selection)
    if state.get("focus") in rows:
        table.focus(state["focus"])
    table.xview_moveto(state.get("x", 0))
    table.yview_moveto(state.get("y", 0))
