"""Auto and fit must agree; profile refinement cannot erase group membership."""

import copy
import json

import numpy as np
import pytest

from spin_backend import pack_spin, run_selected_region, unpack_spin
from spin_groups import candidate_centers, component_matches, recipe_for_groups, remap_groups
from spin_tuning import suggest_parameters
from spintegrate.core import (
    BaselineSettings,
    FitSettings,
    Session,
    baseline_correct,
    lorentzian,
    pick_peaks,
)
from test_spin_bridge import record, recipe


def noisy_six():
    x = np.linspace(0, 1, 2001)
    y = sum(
        lorentzian(x, c, w, h)
        for c, w, h in zip(
            [0.12, 0.25, 0.4, 0.54, 0.69, 0.85],
            [0.008, 0.007, 0.009, 0.006, 0.008, 0.007],
            [1200, 900, 1600, 1100, 500, 350],
        )
    )
    y += np.random.default_rng(31).normal(0, 2.0, len(x)) + 8 * np.sin(
        np.arange(len(x)) * 2 * np.pi / 11
    )
    r = record()
    r.update(
        ppm=x,
        real=y,
        imaginary=np.zeros_like(x),
        corrected=None,
        spectral_width_hz=400 * np.median(np.diff(x)) * len(x),
    )
    p = recipe()
    p["fit"].update(threshold=30.0, line_shape="pseudo_voigt", refine_centers=True)
    return r, p


def test_prominence_rejects_ripple_candidates_without_forcing_a_fixed_number():
    r, _ = noisy_six()
    robust = FitSettings(threshold=30)
    legacy = FitSettings(threshold=30, peak_detection="height")
    assert len(pick_peaks(r["ppm"], r["real"], legacy)[0]) > 6
    assert len(pick_peaks(r["ppm"], r["real"], robust)[0]) == 6
    # A seventh real signal must not be discarded because this experiment had six.
    r["real"] += lorentzian(r["ppm"], 0.95, 0.004, 200)
    assert len(pick_peaks(r["ppm"], r["real"], robust)[0]) == 7


def test_auto_count_matches_actual_model_and_group_survives_shape_switch():
    r, p = noisy_six()
    tuned, note = suggest_parameters(r, p, 0.0, 1.0)
    assert tuned["fit"]["peak_detection"] == "prominence"
    before = run_selected_region(r, tuned, 0.0, 1.0)
    count = len(before["models"][0]["centers"])
    assert count == 6
    assert f"линий-кандидатов {count}" in note
    before["groups"] = [
        dict(
            id="ends",
            name="Пара",
            components=[0, 1],
            missing=0,
            line_shape="asymmetric_pseudo_voigt",
        )
    ]
    changed = run_selected_region(r, recipe_for_groups(tuned, before), 0.0, 1.0)
    remap_groups(before, changed)
    assert len(changed["models"][0]["centers"]) == count
    assert changed["groups"][0]["components"] == [0, 1]
    assert changed["groups"][0]["missing"] == 0
    assert changed["models"][0]["line_shapes"][:2] == ["asymmetric_pseudo_voigt"] * 2
    restored = unpack_spin(json.loads(json.dumps(pack_spin(changed))))
    np.testing.assert_allclose(candidate_centers(restored), candidate_centers(changed))


def test_nearby_noise_elsewhere_does_not_shrink_every_line_tolerance():
    old = [0.0, 0.2, 0.5000, 0.5002, 0.8]
    moved = [0.0001, 0.202, 0.5000, 0.5002, 0.803]
    matches = component_matches(old, moved, 0.005)
    assert matches == {i: i for i in range(5)}
    # An actually removed line must not silently become its close neighbour.
    matches = component_matches(old, [0.0, 0.2, 0.5002, 0.8], 0.005)
    assert 2 not in matches and matches[3] == 2


def test_detected_centers_not_fitted_centers_identify_a_line_across_refinement():
    old = dict(
        sf_mhz=400,
        models=[
            dict(
                centers=np.array([3.4, 3.401, 3.6]),
                picked_centers=np.array([3.4, 3.401, 3.6]),
                diagnostics=dict(center_shifts_hz=[0, 0, 0]),
                x=np.linspace(3, 4, 4001),
            )
        ],
        groups=[dict(id="C", name="C", components=[2], missing=0)],
    )
    new = copy.deepcopy(old)
    new["models"][0]["centers"][2] += 0.006
    new["models"][0]["diagnostics"]["center_shifts_hz"][2] = 2.4
    remap_groups(old, new)
    assert new["groups"][0]["components"] == [2] and new["groups"][0]["missing"] == 0
    # The same identity is reconstructed in older saved models without the new array.
    new["models"][0].pop("picked_centers")
    np.testing.assert_allclose(candidate_centers(new), [3.4, 3.401, 3.6])
    new["models"][0]["centers"] = np.array([3.4, 3.401])
    remap_groups(old, new)
    assert new["groups"][0]["missing"] == 1


def test_snip_does_not_extrapolate_artificial_negative_pedestal():
    x = np.linspace(0.0, 0.5, 401)
    y = lorentzian(x, 0.1, 0.012, 1e6) + lorentzian(x, 0.18, 0.01, 800000)
    corrected, background, _ = baseline_correct(
        x, y, BaselineSettings(method="snip", half_window=69, smooth_half_window=1)
    )
    assert background.min() >= y.min() - 1e-8
    np.testing.assert_allclose(corrected + background, y)


def test_constant_ppm_shift_preserves_detected_peak_identity():
    r, p = record(), recipe()
    old = run_selected_region(r, p, 3.4, 3.7)
    old["groups"] = [dict(id="A", name="A", components=[0, 1], missing=0)]
    r["ppm"] = r["ppm"] + .03
    r["ppm_shift"] = .03
    new = run_selected_region(r, p, 3.43, 3.73)
    remap_groups(old, new)
    assert new["groups"][0]["components"] == [0, 1]
    assert new["groups"][0]["missing"] == 0


@pytest.mark.parametrize(
    "changes",
    [dict(peak_detection="wrong"), dict(min_prominence=-1), dict(min_prominence=float("nan"))],
)
def test_detector_controls_are_validated(changes):
    with pytest.raises(ValueError):
        Session(fit=FitSettings(**changes)).validate()
