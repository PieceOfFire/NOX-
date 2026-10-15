"""The bridge keeps mode/data isolation and explicit physical area units."""

import copy
from dataclasses import asdict
import json

import numpy as np
import pytest

from spin_backend import (
    data_snapshot,
    default_recipe,
    fit_current,
    model_area,
    pack_data,
    pack_spin,
    processed_from_raw,
    recipe_session,
    run_analysis,
    same_state,
    unpack_data,
    unpack_spin,
)
from spintegrate.core import BaselineSettings, FitSettings, ProcessingSettings, lorentzian
from spintegrate.demo import demo_raw


def record():
    x = np.linspace(3, 4, 4001)
    y = lorentzian(x, 3.5, 0.006, 12000) + lorentzian(x, 3.58, 0.009, 6500)
    return dict(
        ppm=x,
        real=y,
        imaginary=np.zeros_like(x),
        corrected=None,
        spectral_width_hz=400 * np.median(np.diff(x)) * len(x),
        ppm_shift=0,
        phase0=0,
        phase1=0,
        phase_pivot=0.5,
        phase_reference=None,
        source_folder="synthetic",
        title="synthetic",
        work_mode="nox",
        integrals=[],
    )


def recipe():
    return dict(
        source="current",
        auto_threshold=False,
        processing=asdict(ProcessingSettings()),
        fit=asdict(FitSettings(threshold=1500)),
        baseline_default=asdict(BaselineSettings(method="none")),
        regions=[
            dict(name="Линии", low=3, high=4, baseline=asdict(BaselineSettings(method="none")))
        ],
    )


def fitted_record():
    r = record()
    data, spin = run_analysis(r, recipe())
    r.update(data, spin=spin, work_mode="spintegrate")
    return r


def test_no_mutation_and_physical_area_not_truncated_tails():
    r = record()
    before = copy.deepcopy(r)
    data, spin = run_analysis(r, recipe())
    assert same_state(r, before)
    r.update(data, spin=spin, work_mode="spintegrate")
    assert fit_current(r)
    expected = np.pi * (12000 * 0.006 + 6500 * 0.009)
    assert model_area(r, 3.4, 3.7) == pytest.approx(expected, rel=0.01)
    assert model_area(r, 3.499, 3.501) == pytest.approx(np.pi * 12000 * 0.006, rel=0.01)
    assert model_area(r, 3.7, 3.8) == 0


def test_unfitted_and_out_of_parent_are_not_silent_zero():
    r = record()
    assert np.isnan(model_area(r, 3.4, 3.7))
    r = fitted_record()
    assert np.isnan(model_area(r, 2.9, 3.7))
    assert np.isnan(model_area(r, 3.4, 4.1))


def test_manual_phase_and_baseline_make_fit_stale():
    r = fitted_record()
    r["phase0"] = 5
    assert not fit_current(r)
    assert np.isnan(model_area(r, 3.4, 3.7))
    r["phase0"] = 0
    r["corrected"] = r["corrected"].copy() + 1
    assert not fit_current(r)
    r["corrected"] = r["spin"]["expected"]["corrected"].copy()
    assert fit_current(r)


def test_alignment_offsets_and_global_x_shift_do_not_change_area():
    r = fitted_record()
    expected = model_area(r, 3.4, 3.7)
    assert model_area(r, 3.41, 3.71, x_offset=0.01) == pytest.approx(expected)
    r["ppm"] = r["ppm"] + 0.003
    r["ppm_shift"] += 0.003
    assert fit_current(r)
    assert model_area(r, 3.403, 3.703) == pytest.approx(expected)


def test_json_roundtrip_does_not_refit_or_change_units():
    r = fitted_record()
    encoded = json.loads(
        json.dumps(dict(data=pack_data(data_snapshot(r)), spin=pack_spin(r["spin"])))
    )
    restored = {**r, **unpack_data(encoded["data"]), "spin": unpack_spin(encoded["spin"])}
    assert same_state(data_snapshot(r), data_snapshot(restored))
    assert model_area(restored, 3.4, 3.7) == model_area(r, 3.4, 3.7)
    assert same_state(restored["spin"], r["spin"])


def test_signed_lines_use_absolute_components_only_in_absolute_mode():
    r = fitted_record()
    r["spin"]["models"][0]["amplitudes"] = np.array([100, -100])
    r["spin"]["models"][0]["widths"] = np.array([0.006, 0.006])
    assert model_area(r, 3.4, 3.7) == pytest.approx(0)
    assert model_area(r, 3.4, 3.7, absolute=True) == pytest.approx(np.pi * 1.2)
    assert model_area(r, 3.57, 3.59) == pytest.approx(-np.pi * 0.6)


def test_overlapping_processing_regions_are_rejected():
    r = recipe()
    r["regions"].append(
        dict(name="Вторая", low=3.5, high=4.2, baseline=asdict(BaselineSettings(method="none")))
    )
    with pytest.raises(ValueError, match="перекрываться"):
        recipe_session(r)


def test_raw_processing_keeps_phased_quadrature_for_nox(tmp_path):
    raw = demo_raw()
    path = tmp_path / "1.npz"
    np.savez(path, fid=raw.fid, sf_mhz=raw.sf_mhz, sw_hz=raw.sw_hz, carrier_ppm=raw.carrier_ppm)
    for mode in ("none", "acme", "abs"):
        processing = asdict(ProcessingSettings(phase=mode, lb_hz=0))
        spectrum, data = processed_from_raw(path, processing)
        np.testing.assert_allclose(data["real"], spectrum.intensity)
        assert np.isfinite(data["imaginary"]).all()
        assert data["phase0"] == data["phase1"] == 0  # no double application of raw phase


def test_current_mode_ignores_fid_settings_and_preserves_nox_backup():
    r = record()
    base_recipe = recipe()
    first, _ = run_analysis(r, base_recipe)
    base_recipe["processing"].update(lb_hz=20, phase="abs", zero_fill_factor=8)
    second, _ = run_analysis(r, base_recipe)
    np.testing.assert_array_equal(first["corrected"], second["corrected"])
    r["nox_backup"] = data_snapshot(r)
    r["real"] = r["real"] * 2
    # Returning to NOX and editing it must not keep fitting a stale old backup.
    third, _ = run_analysis(r, base_recipe)
    np.testing.assert_allclose(third["corrected"], 2 * first["corrected"])


def test_reanalysis_respects_manual_phase_in_spin_workspace():
    r = fitted_record()
    backup = record()
    r["nox_backup"] = data_snapshot(backup)
    r["imaginary"] = r["real"] * 0.5
    r["phase0"] = 15
    r["corrected"] = None
    original = copy.deepcopy(r["nox_backup"])
    data, spin = run_analysis(r, recipe())
    assert data["phase0"] == 15
    assert spin["input_snapshot"]["phase0"] == 15
    assert same_state(r["nox_backup"], original)
    assert not np.array_equal(data["corrected"], r["spin"]["expected"]["corrected"])


def test_repeat_fit_does_not_subtract_local_baseline_twice():
    r = fitted_record()
    first = r["spin"]["models"][0]["amplitudes"].copy()
    _, spin = run_analysis(r, recipe())
    np.testing.assert_array_equal(spin["models"][0]["amplitudes"], first)


def test_parameters_never_propose_regions_even_with_existing_integrals():
    r = record()
    r["integrals"] = [{"left": 3.4, "right": 3.51}, {"left": 3.51, "right": 3.7}]
    proposed = default_recipe(r)
    assert proposed["regions"] == []
    assert proposed["source"] == "current"


def test_selected_region_never_mutates_spectrum_or_integrals():
    from spin_backend import run_selected_region, selected_region_area

    r = record()
    r["integrals"] = [{"id": 1, "left": 3.2, "right": 3.8, "number": 1}]
    before = copy.deepcopy(r)
    settings = recipe()
    settings["regions"] = []
    state = run_selected_region(r, settings, 3.4, 3.7)
    assert same_state(r, before)
    assert state["models"][0]["low"] == 3.4
    assert state["models"][0]["high"] == 3.7
    assert selected_region_area(r, state, 3.4, 3.7) == pytest.approx(np.pi * 130.5, rel=0.02)
    assert not np.isnan(selected_region_area(r, state, 3.4, 3.7))


def test_overlapping_selections_are_independent_parent_fits():
    from spin_backend import run_selected_region, selected_region_area

    r = record()
    a = run_selected_region(r, recipe(), 3.2, 3.7)
    b = run_selected_region(r, recipe(), 3.3, 3.9)
    assert selected_region_area(r, a, 3.2, 3.7) == pytest.approx(
        selected_region_area(r, b, 3.3, 3.9), rel=0.02
    )


def test_per_spectrum_threshold_adapts_after_scaling():
    r = record()
    p = recipe()
    p["auto_threshold"] = True
    _, first = run_analysis(r, p)
    r["real"] = r["real"] * 0.1
    _, second = run_analysis(r, p)
    assert second["threshold"] == pytest.approx(first["threshold"] * 0.1)
    np.testing.assert_allclose(
        second["models"][0]["amplitudes"], first["models"][0]["amplitudes"] * 0.1
    )


def test_bruker_sf_reference_matches_nox_instead_of_acquisition_frequency(tmp_path):
    import nmrglue as ng
    from base_line import load_bruker_spectrum

    size, sf_acq, sf_ref, sw = 4096, 300.00165, 300.00066, 6000
    freq = 600.0
    t = np.arange(size) / sw
    fid = 10000 * np.exp(-np.pi * 4 * t) * np.exp(2j * np.pi * freq * t)
    udic = ng.fileiobase.create_blank_udic(1)
    udic[0].update(size=size, sw=sw, obs=sf_acq, car=1650, complex=True)
    dic = ng.bruker.create_dic(udic)
    dic["acqus"].update(SFO1=sf_acq, SW_h=sw, O1=1650, GRPDLY=0, DECIM=1, DTYPA=2)
    ng.bruker.write(str(tmp_path), dic, fid, isfloat=True, write_prog=False)
    pdata = tmp_path / "pdata" / "1"
    pdata.mkdir(parents=True)
    carrier = (sf_acq - sf_ref) * 1e6 / sf_ref
    offset = carrier + sw / sf_ref / 2
    (pdata / "procs").write_text(
        f"##$SI= {size}\n##$OFFSET= {offset}\n##$SW_p= {sw}\n##$SF= {sf_ref}\n"
        "##$DTYPP= 2\n##$BYTORDP= 0\n",
        encoding="ascii",
    )
    np.zeros(size, dtype="<f8").tofile(pdata / "1r")
    spectrum, _ = processed_from_raw(tmp_path, asdict(ProcessingSettings(lb_hz=0)))
    nox_ppm, *_ = load_bruker_spectrum(tmp_path)
    # fft has the negative Nyquist bin; the descending procs axis starts at the
    # positive Nyquist bin. Their endpoint conventions differ by one FFT step,
    # not by the acquisition/reference carrier shift that caused the real bug.
    step = sw / sf_ref / size
    np.testing.assert_allclose(spectrum.ppm[::-1] + step, nox_ppm, rtol=0, atol=1e-10)
    expected = carrier + freq / sf_ref
    observed = spectrum.ppm[np.argmax(np.abs(spectrum.complex_spectrum))]
    assert observed == pytest.approx(expected, abs=sw / sf_ref / size)
