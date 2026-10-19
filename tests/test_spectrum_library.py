"""Public synthetic fixtures only: acquisition text and library processing indicators."""

import numpy as np
import pytest

from spectrum_library import (
    clean_title,
    library_status,
    matches_search,
    read_bruker_title,
    spectrum_details,
    spectrum_label,
)
from spectrum_state import SpectrumState
from spin_backend import data_snapshot


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1251"])
def test_reads_plain_bruker_title_without_renaming_spectrum(tmp_path, encoding):
    folder = tmp_path / "11" / "pdata" / "1"
    folder.mkdir(parents=True)
    text = "Pd-катализатор\r\n  После реакции, 32 скана  \n"
    (folder / "title").write_bytes(text.encode(encoding))
    title = read_bruker_title(folder)
    assert title == "Pd-катализатор\nПосле реакции, 32 скана"
    record = {"name": "11", "bruker_title": title, "source_folder": folder}
    assert spectrum_label(record) == "11 · Pd-катализатор"
    assert record["name"] == "11"
    assert matches_search(record, "КАТАЛИЗАТОР 32")
    assert matches_search(record, "11 pdata")
    assert not matches_search(record, "неизвестное")


def test_missing_binary_or_oversized_title_does_not_break_loading(tmp_path):
    assert read_bruker_title(tmp_path) == ""
    for raw in (b"binary\0title", b"a" * 65537):
        (tmp_path / "title").write_bytes(raw)
        assert read_bruker_title(tmp_path) == ""
    assert clean_title(None) == ""
    assert clean_title("  a\x00\x01b\t  c \n") == "ab c"
    assert len(clean_title("a" * 8000)) == 4096


def sample():
    return SpectrumState(
        id=1, name="11", ppm=np.linspace(6, 5, 11), real=np.ones(11), imaginary=None
    )


def test_status_distinguishes_processing_from_quality_and_does_not_mutate_arrays():
    record = sample()
    assert library_status(record).summary == "Исходный"
    record["phase0"] = 0.0001
    record["corrected"] = np.zeros(11)
    status = library_status(record)
    assert status.phase_changed and status.baseline_applied
    assert status.summary == "Фаза · Фон"
    assert "без изменений" not in spectrum_details(record)
    assert "правильн" not in spectrum_details(record)
    assert np.array_equal(record["corrected"], np.zeros(11))


def test_status_detects_stale_fit_and_lost_or_missing_group():
    record = sample()
    state = {
        "expected": data_snapshot(record),
        "models": [{"centers": [5.4, 5.6]}],
        "groups": [{"id": "g", "name": "Продукт", "components": [0, 1], "missing": 0}],
    }
    record["spin_analyses"] = {"f": state}
    record["last_spin_fit"] = "f"
    record["integrals"] = [{"name": "Продукт", "spin_fit_id": "f", "spin_group_id": "g"}]
    assert library_status(record).fit_state == "current"
    record["phase0"] = 1
    assert library_status(record).fit_state == "stale"
    assert "пересчитайте" in " ".join(library_status(record).issues)
    record["phase0"] = 0
    state["groups"][0]["missing"] = 1
    assert library_status(record).fit_state == "missing"
    assert "Продукт" in " ".join(library_status(record).issues)
    state["groups"] = []
    assert library_status(record).fit_state == "missing"
    record["spin_analyses"] = {}
    assert library_status(record).fit_state == "missing"


def test_constant_x_shift_keeps_fit_current_but_invalid_component_is_an_issue():
    record = sample()
    record["spin_analyses"] = {
        "f": {"expected": data_snapshot(record), "models": [{"centers": [5.4]}], "groups": []}
    }
    record["last_spin_fit"] = "f"
    record["ppm"] = record["ppm"] + 0.002
    record["ppm_shift"] = 0.002
    assert library_status(record).fit_state == "current"
    record["spin_analyses"]["f"]["groups"] = [{"id": "g", "name": "I1", "components": [4]}]
    assert library_status(record).fit_state == "missing"
