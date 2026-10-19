"""Canonical state invariants independent of Tk and private measurements."""

import copy

import numpy as np
import pytest

from base_line import phase_correct
from spectrum_binding import ActiveSpectrum
from spectrum_state import SpectrumState, SpectrumStore, current_signal, same_state
from spin_backend import data_snapshot, fit_current


def sample():
    x = np.linspace(6, 4, 101)
    return SpectrumState(
        id=1,
        ppm=x,
        real=np.cos(x),
        imaginary=np.sin(x),
        spectral_width_hz=800,
        phase0=2,
        phase1=-4,
    )


def test_defaults_and_detached_state_are_not_shared():
    a, b = SpectrumState(), SpectrumState()
    a["integrals"].append({"id": 1})
    a["baseline_steps"].append({"method": "arpls"})
    a["spin_analyses"]["fit"] = {}
    assert not b["integrals"] and not b["baseline_steps"] and not b["spin_analyses"]
    store = SpectrumStore()
    store.current["ppm"] = np.arange(10)
    store[1] = sample()
    store.active_id = 1
    store.clear()
    assert store.active_id is None and store.current["ppm"] is None


def test_legacy_fields_are_live_views_even_after_replacing_the_record():
    class Client(ActiveSpectrum):
        pass

    app = Client()
    app.initialize_spectrum_store()
    app.spectra[1] = dict(sample())
    app.active_spectrum_id = 1
    assert isinstance(app.spectra[1], SpectrumState)
    app.phase0 = 0.00000001
    assert app.spectra[1]["phase0"] == 0.00000001
    app.spectra[1]["phase1"] = -5
    assert app.phase1 == -5
    replacement = dict(sample(), phase0=13)
    app.spectra[1] = replacement
    assert app.phase0 == 13
    app.integrals.append({"id": 5})
    assert app.spectra[1]["integrals"] == [{"id": 5}]
    with pytest.raises(KeyError):
        app.active_spectrum_id = 99


def test_snapshot_restores_baseline_fit_groups_and_pivot_as_one_unit():
    state = sample()
    state.update(
        corrected=state["real"].copy(),
        phase_pivot=0.4,
        baseline_steps=[{"method": "arpls", "parameters": {"lam": 1e6}}],
        spin_analyses={"fit": {"model": np.arange(10), "groups": [{"id": "group"}]}},
        last_spin_fit="fit",
        integrals=[{"id": 1, "spin_group_id": "group"}],
    )
    before = state.snapshot()
    state["corrected"][0] += 1
    state["spin_analyses"]["fit"]["groups"][0]["id"] = "changed"
    state["phase_pivot"] = 0.6
    state["baseline_steps"] = []
    state["integrals"] = []
    assert not same_state(state.snapshot(), before)
    state.restore(before)
    assert same_state(state.snapshot(), before)
    state["corrected"][0] += 1
    state["spin_analyses"]["fit"]["model"][0] += 1
    assert before["spin_analyses"]["fit"]["model"][0] == 0
    assert state["real"][0] == before["corrected"][0]


def test_snapshot_detects_tiny_changes_and_nested_model_arrays():
    state = sample()
    before = state.snapshot()
    state["phase0"] += 1e-8
    assert not same_state(state.snapshot(), before)
    state.restore(before)
    state["spin_analyses"]["fit"] = {"curve": np.ones(3)}
    assert not same_state(state.snapshot(), before)


def test_signal_is_shared_and_corrected_signal_is_not_rephased():
    state = sample()
    np.testing.assert_array_equal(
        current_signal(state),
        phase_correct(state["real"], state["imaginary"], 2, -4, 0.5),
    )
    corrected = np.arange(len(state["ppm"]), dtype=float)
    state["corrected"] = corrected
    assert current_signal(state) is corrected


@pytest.mark.parametrize(
    "key",
    ["real", "imaginary", "corrected", "phase0", "phase1", "phase_pivot", "spectral_width_hz"],
)
def test_fit_checks_all_processing_inputs_even_when_corrected_exists(key):
    state = sample()
    state["corrected"] = state["real"].copy()
    state["spin"] = {"expected": data_snapshot(state)}
    assert fit_current(state)
    original = copy.deepcopy(state[key])
    if isinstance(original, np.ndarray):
        state[key] = original.copy()
        state[key][0] += 1
    else:
        state[key] += 0.001
    assert not fit_current(state)
    state[key] = original
    assert fit_current(state)


def test_calibration_is_allowed_but_scale_deformation_is_not():
    state = sample()
    state["spin"] = {"expected": data_snapshot(state)}
    state["ppm"] = state["ppm"] + 0.00003
    state["ppm_shift"] += 0.00003
    assert fit_current(state)
    state["ppm"][10] += 0.00001
    assert not fit_current(state)


def test_store_update_and_worker_copy_keep_canonical_and_detached_states():
    store = SpectrumStore()
    store.update({1: dict(sample())})
    assert isinstance(store[1], SpectrumState)
    assert isinstance(store.setdefault(2, dict(sample())), SpectrumState)
    store |= {3: dict(sample())}
    assert isinstance(store[3], SpectrumState)
    assert store[3].snapshot()["phase0"] == 2
    worker = data_snapshot(store[1])
    worker["real"][0] += 1
    assert worker["real"][0] != store[1]["real"][0]
