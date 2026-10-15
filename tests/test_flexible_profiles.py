"""Flexible profiles must improve fit and retain correct full areas and projects."""

import copy
import json

import numpy as np
import pytest

from spintegrate.core import (
    BaselineSettings,
    FitRegion,
    FitSettings,
    Session,
    Spectrum,
    fit_region,
    peak_profile,
    peak_area,
    r_squared,
)
from spin_backend import (
    run_analysis,
    model_area,
    model_component,
    model_component_areas,
    pack_spin,
    unpack_spin,
    same_state,
)
from spin_groups import group_area
from signal_quality import fit_quality
from test_spin_bridge import record, recipe


def sample(mixing=(0.2, 0.7)):
    x = np.linspace(3, 4, 1601)
    centers = np.array([3.50037, 3.55013])
    widths = np.array([0.006, 0.009])
    amplitudes = np.array([12000, 6500])
    curves = peak_profile(x[:, None], centers, widths, amplitudes, np.asarray(mixing))
    y = curves.sum(axis=1)
    return x, y, centers, widths, amplitudes


@pytest.mark.parametrize(
    "shape,mixing", [("pseudo_voigt", (0.2, 0.7)), ("gaussian", (0, 0)), ("lorentzian", (1, 1))]
)
def test_profiles_recover_off_grid_centers_widths_and_full_areas(shape, mixing):
    x, y, centers, widths, amplitudes = sample(mixing)
    spectrum = Spectrum(x, y, y.astype(complex), 400, 400, "synthetic", (0, 0), [])
    region = FitRegion("fit", 3, 4, BaselineSettings(method="none"))
    settings = Session(
        fit=FitSettings(threshold=1000, line_shape=shape, refine_centers=True, initial_width_hz=5)
    )
    result = fit_region(spectrum, region, settings)
    assert result.success
    np.testing.assert_allclose(result.centers, centers, atol=1e-7, rtol=0)
    np.testing.assert_allclose(result.widths, widths, rtol=1e-4)
    np.testing.assert_allclose(result.amplitudes, amplitudes, rtol=1e-4)
    np.testing.assert_allclose(result.mixing, mixing, atol=1e-4)
    np.testing.assert_allclose(result.components().sum(axis=1), result.fitted, atol=1e-8)
    assert result.diagnostics["r_squared"] > 0.999999
    areas = peak_area(result.amplitudes, result.widths, result.mixing)
    np.testing.assert_allclose(areas, peak_area(amplitudes, widths, mixing), rtol=1e-4)
    assert (
        np.max(np.abs(result.diagnostics["center_shifts_hz"])) <= settings.fit.max_center_shift_hz
    )
    if shape == "pseudo_voigt":
        original = fit_region(spectrum, region, Session(fit=FitSettings(threshold=1000)))
        assert np.sum((result.fitted - y) ** 2) < np.sum((original.fitted - y) ** 2) * 0.001


def test_full_area_matches_wide_numerical_integral():
    x = np.linspace(-100, 100, 200001)
    for mixing in (0, 0.4, 1):
        numeric = np.trapezoid(peak_profile(x, 0, 0.02, -100, mixing), x)
        assert numeric == pytest.approx(float(peak_area(-100, 0.02, mixing)), rel=0.001)


def test_r_squared_is_centered_not_clipped_and_handles_constant_signals():
    assert r_squared([1, 2, 3], [1, 2, 3]) == 1
    assert r_squared([1, 2, 3], [2, 2, 2]) == 0
    assert r_squared([1, 2, 3], [10, 10, 10]) < 0
    assert r_squared([3, 3, 3], [3, 3, 3]) is None
    assert r_squared([0, 0, 0], [1, 1, 1]) is None
    assert r_squared([1, 2], [np.nan, 2]) is None
    assert r_squared([], []) is None
    assert r_squared(np.array([1, 2, 3]) * 1e200, np.array([1, 2, 3]) * 1e200) == 1


def test_saved_shapes_drive_groups_formula_areas_and_restored_curves():
    x, y, centers, widths, amplitudes = sample()
    r = record()
    r.update(
        ppm=x,
        real=y,
        imaginary=np.zeros_like(x),
        spectral_width_hz=400 * np.median(np.diff(x)) * len(x),
    )
    settings = recipe()
    settings["fit"].update(line_shape="pseudo_voigt", refine_centers=True)
    data, spin = run_analysis(r, settings)
    r.update(data, spin=spin)
    model = spin["models"][0]
    expected = peak_area(amplitudes, widths, [0.2, 0.7])
    np.testing.assert_allclose(model_component_areas(model), expected, rtol=1e-4)
    assert model_area(r, 3.49, 3.56) == pytest.approx(sum(expected), rel=1e-4)
    spin["groups"] = [dict(id="A", name="A", components=[0, 1], missing=0)]
    assert group_area(r, spin, "A") == pytest.approx(sum(expected), rel=1e-4)
    restored = unpack_spin(json.loads(json.dumps(pack_spin(spin))))
    assert same_state(restored, spin)
    np.testing.assert_allclose(model_component(restored["models"][0], 0), model_component(model, 0))
    assert fit_quality(model)["r_squared"] == pytest.approx(model["diagnostics"]["r_squared"])
    invalid = pack_spin(spin)
    invalid["models"][0]["mixing"] = pack_spin(
        {**spin, "models": [{**model, "mixing": np.array([1.1, 0.2])}]}
    )["models"][0]["mixing"]
    with pytest.raises(ValueError, match="доля Лоренца"):
        unpack_spin(invalid)


def test_original_projects_keep_lorentzian_shape_without_new_settings():
    data, spin = run_analysis(record(), recipe())
    old = copy.deepcopy(spin)
    old["models"][0].pop("mixing")
    for key in ("line_shape", "refine_centers", "max_center_shift_hz"):
        old["recipe"]["fit"].pop(key)
    restored = unpack_spin(json.loads(json.dumps(pack_spin(old))))
    np.testing.assert_allclose(
        model_component(restored["models"][0], 0), model_component(spin["models"][0], 0)
    )
    assert model_area({**record(), **data, "spin": restored}, 3.4, 3.7) == pytest.approx(
        np.pi * 130.5, rel=0.01
    )


def test_signed_pseudo_voigt_areas_and_absolute_output():
    x, _, centers, widths, amplitudes = sample()
    amplitudes[1] *= -1
    mixing = [0.3, 0.8]
    y = peak_profile(x[:, None], centers, widths, amplitudes, mixing).sum(axis=1)
    r = record()
    r.update(
        ppm=x,
        real=y,
        imaginary=np.zeros_like(x),
        spectral_width_hz=400 * np.median(np.diff(x)) * len(x),
    )
    settings = recipe()
    settings["fit"].update(signed=True, line_shape="pseudo_voigt", refine_centers=True)
    data, spin = run_analysis(r, settings)
    r.update(data, spin=spin)
    expected = peak_area(amplitudes, widths, mixing)
    assert model_area(r, 3.49, 3.56) == pytest.approx(sum(expected), rel=1e-4)
    assert model_area(r, 3.49, 3.56, absolute=True) == pytest.approx(sum(abs(expected)), rel=1e-4)


@pytest.mark.parametrize(
    "changes",
    [
        dict(line_shape="unknown"),
        dict(max_center_shift_hz=0),
        dict(max_center_shift_hz=float("nan")),
        dict(refine_centers=1),
    ],
)
def test_invalid_model_controls_are_rejected(changes):
    with pytest.raises(ValueError):
        Session(fit=FitSettings(**changes)).validate()
