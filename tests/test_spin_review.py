"""Comparisons and actionable warnings never change fitted data or group membership."""

import copy

import numpy as np
import pytest

from spin_backend import run_selected_region, same_state
from spin_review import compare_groups, comparison_curve, workspace_issues
from test_spin_bridge import record, recipe


@pytest.fixture
def fitted():
    state = run_selected_region(record(), recipe(), 3.4, 3.7)
    state["groups"] = [
        dict(id="a", name="A", components=[0], missing=0),
        dict(id="b", name="B", components=[1], missing=0),
    ]
    return state


def test_area_changes_keep_reference_detached_and_match_ids(fitted):
    before = copy.deepcopy(fitted)
    fitted["models"][0]["amplitudes"][0] *= 1.1
    original = copy.deepcopy(fitted)
    rows = compare_groups(before, fitted)
    assert rows[0]["delta_pct"] == pytest.approx(10)
    assert rows[1]["delta_pct"] == pytest.approx(0)
    assert same_state(fitted, original)
    assert before["models"][0]["amplitudes"][0] != fitted["models"][0]["amplitudes"][0]


def test_new_group_uses_complete_center_mapping_not_name_or_position(fitted):
    before = copy.deepcopy(fitted)
    fitted["groups"] = [dict(id="new", name="Renamed", components=[1], missing=0)]
    row = compare_groups(before, fitted)[0]
    assert row["delta_pct"] == pytest.approx(0)
    assert "сопоставлены" in row["note"]
    before["models"][0]["centers"] += 0.1
    before["models"][0]["picked_centers"] += 0.1
    row = compare_groups(before, fitted)[0]
    assert row["before"] is None and row["delta_pct"] is None


def test_incomplete_zero_and_changed_membership_are_not_accuracy_claims(fitted):
    before = copy.deepcopy(fitted)
    fitted["groups"][0]["missing"] = 1
    row = compare_groups(before, fitted)[0]
    assert row["after"] is None and row["delta_pct"] is None
    assert "Неполный" in row["note"]
    fitted["groups"][0].update(missing=0, components=[1])
    assert "Изменён состав" in compare_groups(before, fitted)[0]["note"]
    before["models"][0]["amplitudes"][0] = 0
    row = compare_groups(before, fitted)[0]
    assert row["before"] == 0 and row["delta_pct"] is None
    assert "нулю" in row["note"]


def test_signed_and_absolute_area_comparisons(fitted):
    before = copy.deepcopy(fitted)
    before["groups"] = [dict(id="mix", name="Mix", components=[0, 1], missing=0)]
    fitted["groups"] = copy.deepcopy(before["groups"])
    fitted["models"][0]["amplitudes"][1] *= -1
    assert compare_groups(before, fitted)[0]["delta_pct"] < 0
    assert compare_groups(before, fitted, absolute=True)[0]["delta_pct"] == pytest.approx(0)


def test_overlay_accounts_for_local_baseline_and_axis_orientation():
    before = dict(
        models=[
            dict(
                x=np.array([3.0, 2.0, 1.0]),
                fitted=np.array([3.0, 2.0, 1.0]),
                baseline=np.full(3, 2.0),
            )
        ]
    )
    after = dict(models=[dict(x=np.array([1.0, 2.0, 3.0]), baseline=np.full(3, 3.0))])
    np.testing.assert_allclose(comparison_curve(before, after), [0, 1, 2])
    before["expected"] = {"ppm_shift": 0}
    after["expected"] = {"ppm_shift": 0.1}
    after["models"][0]["x"] += 0.1
    np.testing.assert_allclose(comparison_curve(before, after), [0, 1, 2])


def test_group_comparison_survives_constant_x_calibration(fitted):
    before = copy.deepcopy(fitted)
    fitted["expected"]["ppm_shift"] += 0.02
    fitted["models"][0]["centers"] += 0.02
    fitted["models"][0]["picked_centers"] += 0.02
    rows = compare_groups(before, fitted)
    assert all(row["delta_pct"] == pytest.approx(0) for row in rows)
    assert all(row["note"] == "Тот же интеграл" for row in rows)


def test_lost_lines_are_specific_targets_and_unassigned_are_nonblocking(fitted):
    fitted["groups"][1].update(components=[], missing=1)
    centers = fitted["models"][0]["centers"]
    anchors = {(1, "b"): [float(centers[1]) + 0.04]}
    original = copy.deepcopy(fitted)
    issues = workspace_issues({1: {"name": "11"}}, {1: fitted}, anchors=anchors)
    lost = next(i for i in issues if i["kind"] == "missing")
    assert lost["sid"] == 1 and lost["group_id"] == "b"
    assert lost["centers"] == anchors[(1, "b")]
    assert "состав" in lost["action"]
    unused = next(i for i in issues if i["kind"] == "unassigned")
    assert unused["severity"] == "Проверить" and unused["indices"] == [1]
    assert same_state(fitted, original)


def test_problem_targets_include_parameter_and_per_spectrum_fit_failure():
    issues = workspace_issues(
        {}, {}, failures={2: "Не сошлась модель"}, parameter_error="Порог: требуется число"
    )
    assert issues[0]["kind"] == "parameter" and issues[0]["sid"] is None
    assert issues[1]["kind"] == "fit" and issues[1]["sid"] == 2


def test_missing_reference_can_come_from_another_aligned_spectrum(fitted):
    other = copy.deepcopy(fitted)
    missing = float(fitted["models"][0]["centers"][1])
    fitted["groups"][1].update(components=[], missing=1)
    # Make the target candidate genuinely absent while leaving model arrays intact.
    fitted["models"][0]["centers"][1] += 0.1
    fitted["models"][0]["picked_centers"][1] += 0.1
    issues = workspace_issues({}, {1: fitted, 2: other}, offsets={1: 0.01, 2: 0})
    lost = next(i for i in issues if i["kind"] == "missing")
    assert lost["centers"] == pytest.approx([missing - 0.01])
