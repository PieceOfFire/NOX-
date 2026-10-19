"""Actual-record review must not reuse stale worker messages or display units."""

import copy
import json

import numpy as np
import pytest

from batch_diagnostics import inspect_batch_record
from integration import integrate_region
from spin_backend import run_selected_region, same_state
from test_quantitative_workflow import inputs
from test_spin_bridge import recipe


def fitted():
    record, formula = inputs()
    state = run_selected_region(record, recipe(), 3.4, 3.7)
    state["groups"] = [
        dict(id="a", name="A", components=[0], missing=0),
        dict(id="b", name="B", components=[1], missing=0),
    ]
    record["spin_analyses"] = {"fit": state}
    for index, item in enumerate(record["integrals"]):
        item.update(left=3.4, right=3.7, spin_fit_id="fit", spin_group_id="ab"[index])
    return record, formula


def test_physical_areas_formula_and_json_without_mutation():
    record, formula = inputs()
    record["integration_display"] = "Сумма дискретных точек (MestReNova)"
    before = copy.deepcopy(record)
    result = inspect_batch_record(record, formula.to_preset(record["integrals"]))
    assert not result["blocking_issues"]
    a = integrate_region(record["ppm"], record["real"], 3.45, 3.53)
    b = integrate_region(record["ppm"], record["real"], 3.54, 3.65)
    assert result["integrals"] == pytest.approx({"1": a, "2": b})
    assert result["outputs"][0]["value"] == pytest.approx(a / b)
    assert result["outputs"][0]["status"] == "ready"
    assert same_state(before, record)
    json.dumps(result, allow_nan=False)


def test_missing_formula_input_and_zero_division_are_not_zero_outputs():
    record, formula = inputs()
    payload = formula.to_preset(record["integrals"])
    record["integrals"].pop()
    result = inspect_batch_record(record, payload)
    assert result["outputs"][0]["value"] is None
    assert result["blocking_issues"]
    assert "используется 0" not in result["outputs"][0]["message"]
    record, formula = inputs()
    record["real"][:] = 0
    result = inspect_batch_record(record, formula.to_preset(record["integrals"]))
    assert result["integrals"] == {"1": 0.0, "2": 0.0}
    assert result["outputs"][0]["value"] is None
    assert result["blocking_issues"]


def test_empty_result_after_undo_is_not_confirmable_even_without_formula():
    record, _ = inputs()
    record["integrals"] = []
    result = inspect_batch_record(record, {})
    assert result["integrals"] == {} and result["outputs"] == []
    assert any("Нет интегралов" in message for message in result["blocking_issues"])


@pytest.mark.parametrize("kind", ["outside", "narrow", "reversed", "nonfinite"])
def test_invalid_numeric_regions_block_acceptance(kind):
    record, _ = inputs()
    if kind == "outside":
        record["integrals"][0]["left"] = 2
    elif kind == "narrow":
        record["integrals"][0].update(left=3.50001, right=3.50002)
    elif kind == "reversed":
        record["integrals"][0].update(left=3.7, right=3.3)
    else:
        record["real"][0] = np.nan
    result = inspect_batch_record(record)
    assert result["blocking_issues"]
    assert result["integrals"]["1"] is None
    json.dumps(result, allow_nan=False)


def test_missing_group_and_stale_fit_are_blockers_not_numbers():
    record, formula = fitted()
    result = inspect_batch_record(record, formula.to_preset(record["integrals"]))
    assert not result["blocking_issues"]
    assert result["outputs"][0]["value"] == pytest.approx(72 / 58.5, rel=0.02)
    record["spin_analyses"]["fit"]["groups"][0]["missing"] = 1
    result = inspect_batch_record(record)
    assert result["integrals"]["1"] is None
    assert result["incomplete_fits"] == ["fit"]
    # Editing the actual group must clear old worker diagnostics.
    record["spin_analyses"]["fit"]["groups"][0]["missing"] = 0
    assert not inspect_batch_record(record)["incomplete_fits"]
    record["phase0"] = 7
    result = inspect_batch_record(record)
    assert result["integrals"] == {"1": None, "2": None}
    assert result["blocking_issues"] and result["incomplete_fits"]


def test_explicit_empty_manual_group_is_valid_zero_but_automatic_empty_is_not():
    record, _ = fitted()
    group = record["spin_analyses"]["fit"]["groups"][0]
    group["components"] = []
    result = inspect_batch_record(record)
    assert result["integrals"]["1"] == 0
    assert not result["blocking_issues"]
    group["automatic"] = True
    result = inspect_batch_record(record)
    assert result["integrals"]["1"] is None
    assert result["blocking_issues"]


def test_missing_models_and_missing_named_group_are_detected():
    record, _ = fitted()
    record["spin_analyses"]["fit"]["models"] = []
    result = inspect_batch_record(record)
    assert result["blocking_issues"]
    record, _ = fitted()
    record["integrals"][0]["spin_group_id"] = "missing"
    result = inspect_batch_record(record)
    assert result["integrals"]["1"] is None
    assert result["incomplete_fits"] == ["fit"]


def test_numeric_explicit_zero_valid_without_formula_or_with_direct_output():
    record, formula = inputs()
    record["real"][:] = 0
    formula.blocks[4]["input"] = 1
    result = inspect_batch_record(record, formula.to_preset(record["integrals"]))
    assert not result["blocking_issues"]
    assert result["outputs"][0]["value"] == 0
    assert inspect_batch_record(record)["outputs"] == []


def test_absolute_area_mode_and_invalid_formula_are_explicit():
    record, formula = inputs()
    record["real"] -= 3000
    record["integration_method"] = "Сумма модулей относительно 0"
    result = inspect_batch_record(record)
    expected = integrate_region(record["ppm"], record["real"], 3.45, 3.53, absolute=True)
    assert result["integrals"]["1"] == pytest.approx(expected)
    bad = inspect_batch_record(record, {"format": "invalid"})
    assert bad["blocking_issues"] and not bad["outputs"]
    assert not inspect_batch_record(record, {})["outputs"]


def test_duplicate_numbers_and_nonfinite_phase_block_and_stay_json_safe():
    record, _ = inputs()
    record["integrals"][1]["number"] = 1
    record["phase0"] = np.inf
    result = inspect_batch_record(record)
    assert result["blocking_issues"]
    assert result["integrals"]["1"] is None
    assert result["phase_signature"]["phase0"] is None
    json.dumps(result, allow_nan=False)
