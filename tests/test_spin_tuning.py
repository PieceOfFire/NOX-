"""Parameter increments and scientific starting estimates, without Tk."""

import copy
import pytest

from spin_backend import same_state, explain_fit_message
from spin_tuning import adjusted_value, suggest_parameters
from test_spin_bridge import record, recipe
from spintegrate.core import lorentzian


def test_wheel_has_fine_steps_and_reversible_relative_steps():
    assert adjusted_value("fit.initial_width_hz", 5, 1) == pytest.approx(5.1)
    assert adjusted_value("fit.initial_width_hz", 5, 1, fine=True) == pytest.approx(5.01)
    assert adjusted_value("fit.initial_width_hz", 5, 1, coarse=True) == pytest.approx(6)
    increased = adjusted_value("baseline.lam", 1e6, 1)
    assert adjusted_value("baseline.lam", increased, -1) == pytest.approx(1e6)
    assert adjusted_value("fit.threshold", "100,5", 1, fine=True) == pytest.approx(101.0025)
    assert adjusted_value("fit.max_nfev", 2000, 1, fine=True) == 2010
    assert adjusted_value("baseline.p", 0.999, 10) < 1
    assert adjusted_value("fit.restarts", 1, -50) == 1
    with pytest.raises(ValueError):
        adjusted_value("fit.threshold", "nan", 1)


def test_auto_widths_are_physical_and_threshold_tracks_intensity():
    r, settings = record(), recipe()
    original = copy.deepcopy(r)
    tuned, note = suggest_parameters(r, settings, 3.4, 3.7)
    assert 3 < tuned["fit"]["initial_width_hz"] < 9
    assert (
        tuned["fit"]["min_width_hz"]
        < tuned["fit"]["initial_width_hz"]
        < tuned["fit"]["max_width_hz"]
    )
    assert not tuned["auto_threshold"]
    assert "линий-кандидатов 2" in note
    assert same_state(r, original)
    scaled = copy.deepcopy(r)
    scaled["real"] *= 1000
    result, _ = suggest_parameters(scaled, settings, 3.4, 3.7)
    assert result["fit"]["threshold"] == pytest.approx(tuned["fit"]["threshold"] * 1000)
    assert result["fit"]["initial_width_hz"] == pytest.approx(tuned["fit"]["initial_width_hz"])


def test_auto_respects_selected_region_axis_direction_and_offset():
    r = record()
    original, note = suggest_parameters(r, recipe(), 3.45, 3.53)
    assert "линий-кандидатов 1" in note
    reversed_record = copy.deepcopy(r)
    for key in ("ppm", "real", "imaginary"):
        reversed_record[key] = reversed_record[key][::-1]
    tuned, _ = suggest_parameters(reversed_record, recipe(), 3.46, 3.54, offset=0.01)
    assert tuned["fit"]["initial_width_hz"] == pytest.approx(original["fit"]["initial_width_hz"])


def test_auto_detects_bipolar_peaks_and_rejects_flat_signal():
    r = record()
    r["real"] = lorentzian(r["ppm"], 3.5, 0.006, 12000) - lorentzian(r["ppm"], 3.58, 0.009, 6500)
    result, note = suggest_parameters(r, recipe(), 3.4, 3.7)
    assert result["fit"]["signed"] and "линий-кандидатов 2" in note
    r["real"][:] = 1
    with pytest.raises(ValueError, match="уверенных пиков"):
        suggest_parameters(r, recipe(), 3.4, 3.7)


def test_candidate_limit_is_not_an_integral_count_and_auto_keeps_limit():
    settings = recipe()
    settings["fit"]["max_peaks"] = 150
    tuned, _ = suggest_parameters(record(), settings, 3.4, 3.7)
    assert tuned["fit"]["max_peaks"] >= 150
    message = explain_fit_message(
        "найдено 21 пиков (лимит 20). Повысьте threshold/разделение или сузьте область."
    )
    assert "21 линий-кандидатов" in message
    assert "не число итоговых интегралов" in message
