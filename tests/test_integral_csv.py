"""CSV units, missing-value honesty and Excel-safe encoding on public synthetic data."""

import copy
import csv
import io

import numpy as np
import pytest

from integral_csv import (
    DISCRETE,
    build_integration_table,
    integration_value,
    write_integration_csv,
)
from spectrum_state import SpectrumState, same_state
from spin_backend import SPIN_DISPLAY, run_selected_region
from spin_groups import group_area
from test_spin_bridge import record, recipe


def numerical():
    x = np.arange(11, dtype=float)
    r = SpectrumState(id=1, name="Спектр 1", ppm=x, real=1 + 2 * x, imaginary=np.zeros_like(x))
    item = dict(id=1, number=1, name="Продукт", left=2.0, right=8.0)
    r["integrals"] = [item]
    return r, item


@pytest.fixture(scope="module")
def fitted():
    r = record()
    r["name"] = "Модельный спектр"
    state = run_selected_region(r, recipe(), 3.4, 3.7)
    state["groups"] = [dict(id="a", name="Первый сигнал", components=[0], missing=0)]
    r["spin_analyses"] = {"fit": state}
    item = dict(
        id=1,
        number=1,
        name="Первый сигнал",
        left=3.4,
        right=3.7,
        spin_fit_id="fit",
        spin_group_id="a",
    )
    r["integrals"] = [item]
    return r, item


@pytest.mark.parametrize("reverse", [False, True])
def test_ordinary_area_point_sum_and_spin_display_are_explicit(reverse):
    r, item = numerical()
    if reverse:
        for key in ("ppm", "real", "imaginary"):
            r[key] = r[key][::-1].copy()
    assert integration_value(r, item) == 66
    assert integration_value(r, item, display=DISCRETE) == 77
    # Spintegrate's A*w unit only rescales model integrals, not numerical ones.
    assert integration_value(r, item, display=SPIN_DISPLAY) == 66
    r["corrected"] = np.full_like(r["real"], 9)
    assert integration_value(r, item) == 54
    assert integration_value(r, item, display=DISCRETE) == 63


def test_comparison_offset_moves_bounds_not_the_record_or_model(fitted):
    r, item = numerical()
    moved = {**item, "left": 2.125, "right": 8.125}
    assert integration_value(r, moved, offset=0.125) == integration_value(r, item)
    assert integration_value(r, moved, offset=0.125, display=DISCRETE) == 77
    model, group = fitted
    assert integration_value(
        model, {**group, "left": 3.43, "right": 3.73}, offset=0.03
    ) == pytest.approx(integration_value(model, group))


def test_model_full_area_not_truncated_and_unit_conversion(fitted):
    r, item = fitted
    area = group_area(r, r["spin_analyses"]["fit"], "a")
    assert area == pytest.approx(np.pi * 72, rel=0.02)
    assert integration_value(r, item) == pytest.approx(area)
    assert integration_value(r, {**item, "left": 3.49, "right": 3.51}) == pytest.approx(area)
    assert integration_value(r, item, display=DISCRETE) == pytest.approx(
        area / abs(np.median(np.diff(r["ppm"])))
    )
    assert integration_value(r, item, display=SPIN_DISPLAY) == pytest.approx(area / np.pi)


def test_missing_stale_and_invalid_group_are_empty_not_zero(fitted):
    for mutation in ("missing_fit", "missing_group", "missing_line", "invalid_member", "stale"):
        r, item = copy.deepcopy(fitted)
        state = r["spin_analyses"]["fit"]
        if mutation == "missing_fit":
            r["spin_analyses"] = {}
        elif mutation == "missing_group":
            state["groups"] = []
        elif mutation == "missing_line":
            state["groups"][0]["missing"] = 1
        elif mutation == "invalid_member":
            state["groups"][0]["components"] = [999]
        else:
            r["phase0"] = 5
        with pytest.raises(ValueError, match="недоступны|устарели"):
            integration_value(r, item)
        headers, rows, warnings = build_integration_table([dict(record=r, items=[item])])
        assert warnings == 1 and rows[0][headers.index("I1")] is None
        assert rows[0][-1].startswith("I1:")


def test_real_zero_and_explicit_empty_group_remain_zero(fitted):
    r, item = numerical()
    r["real"][:] = 0
    assert integration_value(r, item) == 0
    r, item = copy.deepcopy(fitted)
    r["spin_analyses"]["fit"]["groups"][0].update(components=[], missing=0, automatic=False)
    headers, rows, warnings = build_integration_table([dict(record=r, items=[item])])
    assert warnings == 0 and rows[0][headers.index("I1")] == 0


def test_duplicate_and_absent_numbers_have_explanation_and_preserved_columns():
    r, item = numerical()
    duplicate = {**item, "id": 2, "name": "Другой"}
    second = {**item, "number": 3, "name": "Третий"}
    entries = [dict(record=r, items=[item, duplicate]), dict(record=r, items=[second])]
    headers, rows, warnings = build_integration_table(entries)
    assert headers[4:6] == ["I1", "I3"]
    assert rows[0][4:6] == [None, None]
    assert rows[1][4] is None and rows[1][5] == 66
    assert "I1: номер повторяется" in rows[0][-1]
    assert "I3: нет интеграла" in rows[0][-1]
    assert warnings == 3


def test_metadata_stores_each_spectrums_bounds_names_status_and_method():
    r, item = numerical()
    entries = [dict(record=r, items=[item], status="Подтверждён")]
    headers, rows, warnings = build_integration_table(entries, include_bounds=True)
    assert headers == [
        "Спектр",
        "Состояние",
        "Метод",
        "Единицы",
        "I1",
        "I1: имя",
        "I1: от, ppm",
        "I1: до, ppm",
        "Примечания",
    ]
    assert rows[0] == [
        "Спектр 1",
        "Подтверждён",
        "Обычная площадь",
        "Интенсивность × ppm",
        66,
        "Продукт",
        2,
        8,
        "",
    ]
    assert warnings == 0


def test_absolute_method_is_distinct_from_display_units():
    r, item = numerical()
    r["real"] = r["ppm"] - 5
    headers, rows, warnings = build_integration_table(
        [
            dict(record=r, items=[item], method="Сумма модулей относительно 0"),
        ],
        display=DISCRETE,
    )
    assert rows[0][headers.index("I1")] == 12
    assert warnings == 0
    assert integration_value(r, item) == 0
    assert integration_value(r, item, absolute=True) == 9


@pytest.mark.parametrize(
    "item",
    [
        dict(left=-1, right=8),
        dict(left=2, right=11),
        dict(left=8, right=2),
        dict(left=2, right=2.1),
        dict(left=float("nan"), right=8),
    ],
)
def test_invalid_or_too_short_roi_is_not_exported_as_zero(item):
    r, valid = numerical()
    headers, rows, warnings = build_integration_table([dict(record=r, items=[{**valid, **item}])])
    assert warnings == 1 and rows[0][headers.index("I1")] is None
    assert rows[0][-1]


@pytest.mark.parametrize("missing_key", ["left", "right"])
def test_malformed_bounds_with_metadata_leave_cell_empty_not_abort_export(missing_key):
    r, item = numerical()
    del item[missing_key]
    headers, rows, warnings = build_integration_table(
        [dict(record=r, items=[item])], include_bounds=True
    )
    assert rows[0][headers.index("I1")] is None and warnings == 1
    assert rows[0][headers.index("I1: имя")] == "Продукт"
    assert rows[0][headers.index(f"I1: {'от' if missing_key == 'left' else 'до'}, ppm")] is None
    assert rows[0][-1]


@pytest.mark.parametrize("offset", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_offsets_are_rejected_even_for_fixed_model_group(fitted, offset):
    r, item = numerical()
    with pytest.raises(ValueError):
        integration_value(r, item, offset=offset)
    with pytest.raises(ValueError):
        integration_value(fitted[0], fitted[1], offset=offset)


def test_build_table_and_export_do_not_mutate_inputs(tmp_path, fitted):
    r, item = numerical()
    entries = [dict(record=r, items=[item]), dict(record=fitted[0], items=[fitted[1]])]
    before = copy.deepcopy(entries)
    for display in ("Площадь", DISCRETE, SPIN_DISPLAY):
        headers, rows, _ = build_integration_table(entries, display=display, include_bounds=True)
        write_integration_csv(tmp_path / f"{len(display)}.csv", headers, rows)
        assert same_state(entries, before)


def test_utf8_bom_russian_excel_decimal_and_text_quoting(tmp_path):
    path = tmp_path / "таблица.csv"
    headers = ["Спектр", "Значение", "Примечания"]
    rows = [["Pd; образец\nпосле реакции", 1234.25, 'Имя "линии"; проверено'], ["Нет", None, ""]]
    write_integration_csv(path, headers, rows)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "1234,25" in text and "\r\n" in text
    assert list(csv.reader(io.StringIO(text), delimiter=";")) == [
        headers,
        [rows[0][0], "1234,25", rows[0][2]],
        ["Нет", "", ""],
    ]


@pytest.mark.parametrize("separator", [";", ",", "\t"])
def test_separator_decimal_choice_and_spreadsheet_formula_escaping(tmp_path, separator):
    path = tmp_path / "safe.csv"
    dangerous = ["=1+1", "+SUM(A1:A3)", "-1+2", "@SUM(A1:A3)", ' \t=HYPERLINK("x")']
    rows = [
        [text, -2.5, np.float64(1.25), np.int64(7), float("nan"), float("inf")]
        for text in dangerous
    ]
    write_integration_csv(
        path,
        ["=unsafe header", "n", "f", "i", "missing", "inf"],
        rows,
        separator=separator,
        decimal_comma=False,
    )
    parsed = list(
        csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig")), delimiter=separator)
    )
    assert parsed[0][0] == "'=unsafe header"
    for source, row in zip(dangerous, parsed[1:]):
        assert row == ["'" + source, "-2.5", "1.25", "7", "", ""]


def test_no_integrals_unknown_units_or_delimiter_fail_explicitly(tmp_path):
    r, item = numerical()
    with pytest.raises(ValueError, match="Нет интегралов"):
        build_integration_table([dict(record=r, items=[])])
    with pytest.raises(ValueError, match="единицы"):
        integration_value(r, item, display="Неизвестно")
    with pytest.raises(ValueError, match="единицы"):
        build_integration_table([dict(record=r, items=[item])], display="Неизвестно")
    with pytest.raises(ValueError, match="Разделитель"):
        write_integration_csv(tmp_path / "x.csv", [], [], separator="|")
