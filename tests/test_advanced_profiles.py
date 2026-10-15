"""Mixed and asymmetric profiles: curves, areas, group identities and persistence."""

import copy
import json

import numpy as np
import pytest

from spin_backend import (
    model_component,
    model_component_areas,
    model_component_fwhms,
    pack_spin,
    run_analysis,
    same_state,
    unpack_spin,
)
from spin_groups import group_area, recipe_for_groups, remap_groups, validate_groups
from spintegrate.core import BaselineSettings, FitRegion, FitSettings, Session, Spectrum, fit_region
from spintegrate.profiles import component_area, component_curve, component_fwhm, voigt_widths
from test_spin_bridge import record, recipe


@pytest.mark.parametrize("shape", ["voigt", "phased_voigt", "asymmetric_pseudo_voigt"])
def test_curve_full_area_and_fwhm(shape):
    width, extra = (0.01, 0.004) if "voigt" in shape and "pseudo" not in shape else (0.008, 0.014)
    phase = 0.1 if shape == "phased_voigt" else 0.0
    x = np.linspace(-100, 100, 400001)
    y = component_curve(x, 0.0, width, -100.0, 0.35, shape, extra, phase)
    area = component_area(width, -100.0, 0.35, shape, extra, phase)
    assert np.trapezoid(y, x) == pytest.approx(area, rel=1e-3)
    fwhm = component_fwhm(width, 0.35, shape, extra)
    assert fwhm > 0
    if shape == "asymmetric_pseudo_voigt":
        assert fwhm == pytest.approx(width + extra)
    else:
        absorptive = component_curve([0.0, fwhm / 2], 0.0, width, 100.0, 0.35, "voigt", extra)
        assert absorptive[1] == pytest.approx(absorptive[0] / 2, rel=1e-7)


@pytest.mark.parametrize("balance", [0.01, 0.25, 0.5, 0.8, 0.99])
def test_voigt_bounds_refer_to_total_fwhm(balance):
    gamma, sigma = voigt_widths(0.01, balance)
    assert component_fwhm(gamma, balance, "voigt", sigma) == pytest.approx(0.02, rel=1e-6)


@pytest.mark.parametrize("shape", ["voigt", "phased_voigt", "asymmetric_pseudo_voigt"])
def test_flexible_solver_recovers_known_parameters_without_extra_peaks(shape):
    x = np.linspace(3.0, 4.0, 1201)
    centers = [3.4503, 3.5902]
    gamma, sigma = voigt_widths(0.007, 0.55)
    widths = [0.008, 0.01] if shape == "asymmetric_pseudo_voigt" else [gamma, gamma * 1.3]
    extras = [0.013, 0.016] if shape == "asymmetric_pseudo_voigt" else [sigma, sigma * 1.3]
    phase = 0.08 if shape == "phased_voigt" else 0.0
    amps, mixes = [12000, 8500], [0.4, 0.7]
    y = sum(
        component_curve(x, c, w, a, m, shape, e, phase)
        for c, w, a, m, e in zip(centers, widths, amps, mixes, extras)
    )
    spectrum = Spectrum(x, y, y.astype(complex), 400.0, 400.0, "synthetic", (0, 0), [])
    settings = Session(
        fit=FitSettings(
            threshold=1000.0,
            line_shape=shape,
            refine_centers=True,
            initial_width_hz=6.0,
            max_center_shift_hz=5.0,
        )
    )
    result = fit_region(
        spectrum, FitRegion("fit", 3.0, 4.0, BaselineSettings(method="none")), settings
    )
    assert result.success and len(result.centers) == 2
    assert result.diagnostics["r_squared"] > 0.99999999
    np.testing.assert_allclose(result.centers, centers, atol=1e-6, rtol=0)
    np.testing.assert_allclose(result.components().sum(axis=1), result.fitted)
    expected = [
        component_area(w, a, m, shape, e, phase) for w, a, m, e in zip(widths, amps, mixes, extras)
    ]
    np.testing.assert_allclose(result.areas(), expected, rtol=1e-4)


def mixed_sample():
    x = np.linspace(3.0, 4.0, 1401)
    y = component_curve(
        x, 3.45, 0.007, 12000, 0.4, "asymmetric_pseudo_voigt", 0.013
    ) + component_curve(x, 3.62, 0.009, 7000, 1.0, "lorentzian")
    r = record()
    r.update(
        ppm=x,
        real=y,
        imaginary=np.zeros_like(x),
        corrected=None,
        spectral_width_hz=400 * np.median(np.diff(x)) * len(x),
    )
    p = recipe()
    p["fit"].update(
        line_shape="lorentzian",
        refine_centers=True,
        component_shapes=[dict(center=3.45, shape="asymmetric_pseudo_voigt", tolerance=0.005)],
    )
    data, state = run_analysis(r, p)
    r.update(data, spin=state)
    state["groups"] = [
        dict(id="A", name="A", components=[0], missing=0, line_shape="asymmetric_pseudo_voigt"),
        dict(id="B", name="B", components=[1], missing=0),
    ]
    return r, state, p


def test_mixed_groups_curve_area_formula_units_and_project_roundtrip():
    r, state, _ = mixed_sample()
    model = state["models"][0]
    assert model["line_shapes"] == ["asymmetric_pseudo_voigt", "lorentzian"]
    assert model["diagnostics"]["r_squared"] > 0.99999999
    expected = [
        component_area(0.007, 12000, 0.4, "asymmetric_pseudo_voigt", 0.013),
        component_area(0.009, 7000),
    ]
    np.testing.assert_allclose(model_component_areas(model), expected, rtol=1e-4)
    assert group_area(r, state, "A") == pytest.approx(expected[0], rel=1e-4)
    assert group_area(r, state, "B") == pytest.approx(expected[1], rel=1e-4)
    np.testing.assert_allclose(model_component_fwhms(model), [0.02, 0.018], rtol=1e-4)
    restored = unpack_spin(json.loads(json.dumps(pack_spin(state))))
    assert same_state(restored, state)
    np.testing.assert_allclose(model_component(restored["models"][0], 0), model_component(model, 0))
    validate_groups(restored)


def test_group_shape_policy_remaps_and_does_not_use_raw_index_as_identity():
    r, state, p = mixed_sample()
    rebuilt = recipe_for_groups(p, state)
    assert rebuilt["fit"]["component_shapes"][0]["shape"] == "asymmetric_pseudo_voigt"
    assert rebuilt["fit"]["component_shapes"][0]["center"] == pytest.approx(3.45, abs=1e-5)
    _, newer = run_analysis(r, rebuilt)
    remap_groups(state, newer)
    assert newer["groups"][0]["id"] == "A"
    assert newer["groups"][0]["line_shape"] == "asymmetric_pseudo_voigt"
    assert not any(g["missing"] for g in newer["groups"])
    assert recipe_for_groups(p)["fit"]["component_shapes"] == []
    original = copy.deepcopy(state)
    newer["models"][0]["centers"] = newer["models"][0]["centers"][1:]
    remap_groups(original, newer)
    assert newer["groups"][0]["missing"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("extra_widths", [0.0, 0.01]),
        ("profile_phases", [2.0, 0.0]),
        ("line_shapes", ["unknown", "lorentzian"]),
    ],
)
def test_invalid_saved_shape_parameters_are_rejected(field, value):
    _, state, _ = mixed_sample()
    state["models"][0][field] = value if field == "line_shapes" else np.array(value)
    with pytest.raises(ValueError, match="гибкой формы"):
        unpack_spin(pack_spin(state))


@pytest.mark.parametrize(
    "changes",
    [
        dict(max_asymmetry_ratio=1),
        dict(max_profile_phase_degrees=31),
        dict(component_shapes=[dict(center=3.5, shape="wrong")]),
        dict(component_shapes=[dict(center=float("nan"), shape="voigt")]),
    ],
)
def test_advanced_constraints_are_validated(changes):
    with pytest.raises(ValueError):
        Session(fit=FitSettings(**changes)).validate()
