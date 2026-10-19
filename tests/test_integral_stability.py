"""Physical sensitivity, independent factors, honest failures and no mutations."""

import copy
import json
import threading
from unittest.mock import patch

import numpy as np
import pytest

import integral_stability as stability
from integration import integrate_region
from spin_backend import run_selected_region, same_state
from test_quantitative_workflow import inputs
from test_spin_bridge import recipe


def test_all_integrals_without_formula_boundaries_offsets_and_scale():
    record, _ = inputs()
    record["real"] += np.random.default_rng(9).normal(0, 3, len(record["ppm"]))
    before = copy.deepcopy(record)
    rows = stability.analyze_integral_stability(record, record["integrals"], delta=0.002)
    assert len(rows) == 26 and all(r["status"] == "ready" for r in rows)
    assert same_state(record, before)
    item = record["integrals"][0]
    left = next(r for r in rows if r["number"] == 1 and r["case"] == "Левая граница +δ")
    assert left["value"] == pytest.approx(
        integrate_region(record["ppm"], record["real"], item["left"] + 0.002, item["right"])
    )
    shifted_items = copy.deepcopy(record["integrals"])
    for item in shifted_items:
        item["left"] += 0.2
        item["right"] += 0.2
    shifted = stability.analyze_integral_stability(record, shifted_items, offset=0.2, delta=0.002)
    np.testing.assert_allclose([r["value"] for r in shifted], [r["value"] for r in rows], rtol=0.01)
    scaled = copy.deepcopy(record)
    scaled["real"] *= 8
    scaled["ppm"] = scaled["ppm"][::-1]
    scaled["real"] = scaled["real"][::-1]
    scaled["imaginary"] = scaled["imaginary"][::-1]
    again = stability.analyze_integral_stability(scaled, scaled["integrals"], delta=0.002)
    np.testing.assert_allclose(
        [r["percent"] for r in again], [r["percent"] for r in rows], atol=1e-10
    )
    json.dumps(rows, allow_nan=False)


def test_background_offset_and_nox_line_are_independent_exact_changes():
    record, _ = inputs()
    record["corrected"] = record["real"].copy()
    record["real"] += 100
    rows = stability.analyze_integral_stability(record, record["integrals"], baseline_percent=5)
    item = record["integrals"][0]
    subset = [r for r in rows if r["number"] == 1]
    nox = next(r for r in subset if r["case"] == "Вычитание NOX +5%")
    assert nox["value"] == pytest.approx(
        integrate_region(record["ppm"], record["corrected"] - 5, item["left"], item["right"])
    )
    floor = next(r for r in subset if r["case"] == "Уровень фона +1σ")
    assert floor["value"] == pytest.approx(
        integrate_region(
            record["ppm"], record["corrected"] - floor["noise"], item["left"], item["right"]
        )
    )
    assert not any(r["factor"] == "Линия Spintegrate" for r in rows)
    summary = stability.summarize_integral_stability(rows)
    assert len(summary) == 2 and summary[0]["failed"] == 0
    assert {"Границы", "Фон", "Наклон", "Линия NOX"} == set(summary[0]["factors"])


def test_zero_nominal_has_absolute_change_but_never_infinite_percent():
    record, _ = inputs()
    record["real"][:] = 0
    rows = stability.analyze_integral_stability(record, record["integrals"])
    assert all(r["percent"] is None for r in rows)
    assert all(r["value"] == 0 for r in rows)
    assert stability.summarize_integral_stability(rows)[0]["percent"] is None
    json.dumps(rows, allow_nan=False)


def test_invalid_trial_region_is_error_not_zero_and_delta_clamped_to_grid():
    record, _ = inputs()
    item = copy.deepcopy(record["integrals"][0])
    item.update(left=record["ppm"].min(), right=record["ppm"].min() + 0.01)
    rows = stability.analyze_integral_stability(record, [item], delta=1e-8)
    assert rows[0]["delta"] == pytest.approx(abs(np.median(np.diff(record["ppm"]))))
    bad = next(r for r in rows if r["case"] == "Левая граница −δ")
    assert bad["status"] == "error" and bad["value"] is None
    assert stability.summarize_integral_stability(rows)[0]["failed"] > 0


@pytest.mark.parametrize(
    "parameters",
    [
        {"delta": 0},
        {"delta": np.nan},
        {"background_sigma": -1},
        {"baseline_percent": 51},
        {"baseline_percent": np.inf},
    ],
)
def test_invalid_controls_rejected(parameters):
    record, _ = inputs()
    with pytest.raises(ValueError):
        stability.analyze_integral_stability(record, record["integrals"], **parameters)


def fitted():
    record, _ = inputs()
    state = run_selected_region(record, recipe(), 3.4, 3.7)
    state["groups"] = [
        dict(id="a", name="A", components=[0], missing=0),
        dict(id="b", name="B", components=[1], missing=0),
    ]
    record["spin_analyses"] = {"fit": state}
    for i, item in enumerate(record["integrals"]):
        item.update(left=3.4, right=3.7, spin_fit_id="fit", spin_group_id="ab"[i])
    return record


def test_shared_fit_refitted_once_per_case_groups_and_input_preserved():
    record = fitted()
    before = copy.deepcopy(record)
    with patch.object(stability, "run_selected_region", wraps=run_selected_region) as run:
        rows = stability.analyze_integral_stability(record, record["integrals"])
    assert run.call_count == 12  # 8 boundary + 2 level + 2 slope, not twice per group
    assert len(rows) == 26 and all(r["status"] == "ready" for r in rows)
    assert [r["value"] for r in rows[:2]] == pytest.approx([72 * np.pi, 58.5 * np.pi], rel=0.02)
    assert all(call.args[1]["baseline_policy"] == "manual" for call in run.call_args_list)
    assert same_state(record, before)
    record["phase0"] = 2
    stale = stability.analyze_integral_stability(record, record["integrals"])
    assert all(r["status"] == "error" and r["value"] is None for r in stale)


def test_lost_components_are_never_valid_zero_and_cancellation_is_empty():
    record = fitted()

    def vanish(trial, settings, left, right):
        state = run_selected_region(trial, settings, left, right)
        state["models"][0]["centers"] = np.array([])
        return state

    with patch.object(stability, "run_selected_region", side_effect=vanish):
        rows = stability.analyze_integral_stability(record, record["integrals"])
    assert all(r["value"] is None and r["status"] == "error" for r in rows[2:])
    stop = threading.Event()
    stop.set()
    assert stability.analyze_integral_stability(record, record["integrals"], stop=stop) == []


def test_local_lambda_is_separate_and_not_applied_to_numeric_integral():
    record = fitted()
    settings = recipe()
    settings["baseline_default"]["method"] = "asls"
    old = run_selected_region(record, settings, 3.4, 3.7)
    old["groups"] = copy.deepcopy(record["spin_analyses"]["fit"]["groups"])
    record["spin_analyses"]["fit"] = old
    record["integrals"].append(dict(id=3, number=3, name="Numeric", left=3.45, right=3.53))
    lam = old["recipe"]["baseline_default"]["lam"]
    with patch.object(stability, "run_selected_region", wraps=run_selected_region) as run:
        rows = stability.analyze_integral_stability(record, record["integrals"], baseline_percent=5)
    local = [r for r in rows if r["factor"] == "Линия Spintegrate"]
    assert len(local) == 6
    assert all(r["status"] == "skipped" and r["value"] is None for r in local if r["number"] == 3)
    actual = {call.args[1]["baseline_default"]["lam"] for call in run.call_args_list}
    assert actual == {lam, 0.95 * lam, 1.05 * lam}
    assert not any(r["factor"] == "Линия NOX" for r in rows)
    numeric = next(s for s in stability.summarize_integral_stability(rows) if s["number"] == 3)
    assert not numeric["notes"] and numeric["failed"] == 0
