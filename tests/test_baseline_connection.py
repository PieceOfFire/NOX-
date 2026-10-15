import copy
from dataclasses import asdict

import numpy as np
import pytest

from baseline_methods import EXTENDED_METHODS, FIELDS, extended_baseline
from baseline_policy import corrected_region, resolve_spin_baseline
from processing_templates import (
    apply_baseline_steps,
    apply_template,
    make_template,
    validate_template,
)
from spin_backend import default_recipe, run_selected_region
from spintegrate.core import BASELINE_METHODS, BaselineSettings, baseline_correct
from test_quantitative_workflow import inputs
from test_spin_bridge import record


def corrected_record():
    r = record()
    r["corrected"] = r["real"].copy()
    r["baseline_steps"] = [dict(method="Адаптивный arPLS", low=3.4, high=3.7)]
    return r


def test_only_current_correction_covering_whole_roi_disables_local_background():
    r = corrected_record()
    assert corrected_region(r, 3.42, 3.68)
    assert corrected_region(r, 3.45, 3.7, offset=0.02)
    assert not corrected_region(r, 3.3, 3.8)
    assert not corrected_region(r, 3.8, 3.9)
    r["baseline_steps"] = [dict(low=3.4, high=3.5), dict(low=3.5, high=3.7)]
    assert corrected_region(r, 3.42, 3.68)
    r["baseline_steps"][1]["low"] = 3.52
    assert not corrected_region(r, 3.42, 3.68)
    r["corrected"] = None
    assert not corrected_region(r, 3.42, 3.49)  # stale metadata after phase/undo


def test_legacy_unknown_correction_is_not_subtracted_twice_by_default():
    r = corrected_record()
    r["baseline_steps"] = []
    assert corrected_region(r, 3.4, 3.7)
    r["baseline_source_unknown"] = True
    assert corrected_region(r, 3.3, 3.9)


def test_policy_is_per_spectrum_and_manual_override_is_respected():
    recipe = default_recipe()
    before = copy.deepcopy(recipe)
    r = corrected_record()
    corrected = resolve_spin_baseline(recipe, r, 3.42, 3.68)
    assert corrected["baseline_default"]["method"] == "none"
    assert corrected["baseline_fallback_method"] == "snip"
    raw = resolve_spin_baseline(corrected, record(), 3.42, 3.68)
    assert raw["baseline_default"]["method"] == "snip"
    corrected["baseline_policy"] = "manual"
    corrected["baseline_default"]["method"] = "arpls"
    assert resolve_spin_baseline(corrected, r, 3.42, 3.68)["baseline_default"]["method"] == "arpls"
    assert recipe == before


def test_fit_uses_current_corrected_signal_without_another_baseline():
    r = corrected_record()
    recipe = default_recipe()
    recipe["fit"].update(threshold=1500, restarts=1)
    result = run_selected_region(r, recipe, 3.42, 3.68)
    model = result["models"][0]
    assert result["recipe"]["baseline_default"]["method"] == "none"
    np.testing.assert_array_equal(model["baseline"], 0)
    np.testing.assert_allclose(model["corrected"], np.interp(model["x"], r["ppm"], r["corrected"]))
    # Unprocessed member of the same series gets its requested baseline.
    raw = run_selected_region(record(), recipe, 3.42, 3.68)
    assert raw["recipe"]["baseline_default"]["method"] == "snip"
    assert np.any(raw["models"][0]["baseline"])


@pytest.mark.parametrize("method", BASELINE_METHODS)
def test_nox_extended_methods_match_spintegrate_with_descending_axis_and_negative_peaks(method):
    assert set(EXTENDED_METHODS) == set(BASELINE_METHODS)
    x = np.linspace(0, 1, 401)
    signal = 20 + 4 * x + 300 / (1 + ((x - 0.4) / 0.015) ** 2)
    settings = BaselineSettings(method=method)
    _, reference, _ = baseline_correct(x, signal, settings)
    parameters = asdict(settings)
    parameters.pop("method")
    positive, _ = extended_baseline(x[::-1], signal[::-1], method, parameters, polarity=1)
    negative, _ = extended_baseline(x[::-1], -signal[::-1], method, parameters, polarity=-1)
    np.testing.assert_allclose(positive, reference[::-1])
    np.testing.assert_allclose(negative, -reference[::-1])


def test_extended_correction_survives_template_replay_and_validates_parameters():
    r, doc = inputs()
    r["baseline_steps"] = [
        dict(
            method="Spintegrate: snip",
            low=3.4,
            high=3.7,
            polarity=1,
            parameters={key: default for key, (_, default) in FIELDS.items()},
        )
    ]
    r["corrected"] = apply_baseline_steps(r, r["baseline_steps"])
    template = make_template(r, r["integrals"], doc.to_preset(r["integrals"]))
    result, _ = apply_template(record(), template, align=False)
    np.testing.assert_allclose(result["corrected"], r["corrected"])
    invalid = copy.deepcopy(template)
    invalid["baseline_steps"][0]["parameters"]["half_window"] = 3.5
    with pytest.raises(ValueError):
        validate_template(invalid)
