"""UI-only configuration is bounded, recoverable and separate from spectra."""

import json

import pytest

from view_preferences import (
    DEFAULTS,
    capture_table_view,
    normalized_preferences,
    preferences_path,
    read_preferences,
    restore_table_view,
    usable_limits,
    visible_geometry,
    write_preferences,
)


def test_preferences_ignore_untrusted_and_nonfinite_values():
    values = normalized_preferences(
        {
            "geometry": "run something",
            "linked_ppm": "false",
            "auto_y": False,
            "library_fraction": float("inf"),
            "graph_percent": -10,
            "private_data": [1, 2],
        }
    )
    assert values == {**DEFAULTS, "auto_y": False, "graph_percent": 30}
    assert normalized_preferences([]) == DEFAULTS


def test_preferences_round_trip_without_scientific_payload(tmp_path):
    path = tmp_path / "ui" / "preferences.json"
    values = {
        "geometry": "1150x780+42+18",
        "maximized": True,
        "library_visible": False,
        "table_visible": True,
        "linked_ppm": False,
        "spectrum": [1, 2, 3],
    }
    assert write_preferences(values, path)
    loaded = read_preferences(path)
    assert loaded["geometry"] == "1150x780+42+18"
    assert loaded["maximized"]
    assert not loaded["library_visible"] and loaded["table_visible"]
    assert "spectrum" not in json.loads(path.read_text("utf-8"))


def test_preferences_corruption_and_io_failure_do_not_break_launch(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("invalid json", encoding="utf-8")
    assert read_preferences(path) == DEFAULTS
    assert read_preferences(tmp_path / "missing") == DEFAULTS
    assert not write_preferences({}, tmp_path)  # directory, not a file


def test_local_preferences_path_can_be_redirected_for_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("HIMERA_VIEW_PREFERENCES", str(tmp_path / "view.json"))
    assert preferences_path() == tmp_path / "view.json"


def test_geometry_recovers_from_disconnected_display():
    assert visible_geometry("1600x1000+2400+80", (1920, 1080)) == "1600x1000+320+80"
    assert visible_geometry("1500x900-1500-20", (1920, 1080)) == "1500x900+0+0"
    assert visible_geometry("3000x1900+0+0", (1920, 1080)) == "1920x1040+0+0"
    assert visible_geometry("invalid", (1920, 1080)) == "950x620+0+0"


@pytest.mark.parametrize(
    "limits", [None, (), [1], [1, 1], [1, 2, 3], [float("nan"), 1], [1, float("inf")]]
)
def test_limits_reject_invalid_axes(limits):
    assert usable_limits(limits) is None


def test_linked_limits_keep_nmr_direction_but_reject_empty_domain():
    assert usable_limits((5.6, 5.1), (0, 10)) == (5.6, 5.1)
    assert usable_limits((5.6, 5.1), (3, 4)) is None
    assert usable_limits((5.6, 3.1), (3, 4)) == (5.6, 3.1)


class Table:
    def __init__(self):
        self.rows = ["1", "2", "3"]
        self.selected = ("2", "3")
        self.focused = "2"
        self.scroll_x, self.scroll_y = 0.2, 0.4

    def selection(self):
        return self.selected

    def selection_set(self, ids):
        self.selected = tuple(ids)

    def focus(self, iid=None):
        if iid is not None:
            self.focused = iid
        return self.focused

    def xview(self):
        return self.scroll_x, 1

    def yview(self):
        return self.scroll_y, 1

    def xview_moveto(self, value):
        self.scroll_x = value

    def yview_moveto(self, value):
        self.scroll_y = value

    def get_children(self):
        return self.rows


def test_table_refresh_restores_ids_not_row_positions():
    table = Table()
    state = capture_table_view(table)
    table.rows = ["3", "4", "5"]
    table.scroll_x = table.scroll_y = 0
    restore_table_view(table, state)
    assert table.selected == ("3",)
    assert (table.scroll_x, table.scroll_y) == (0.2, 0.4)
