"""Comfortable parameter increments and conservative starting estimates for local fits."""

from __future__ import annotations

import copy
import math
import numpy as np
from spin_backend import current_signal, engine
from spintegrate.peak_detection import detect_peaks, noise_sigma
from baseline_policy import resolve_spin_baseline

# (minimum, maximum, normal step, logarithmic, integer)
PARAMETERS = {
    "fit.threshold": (1e-12, 1e300, 0.05, True, False),
    "fit.min_prominence": (0, 1e300, 100, False, False),
    "fit.min_separation_points": (1, 100000, 1, False, True),
    "fit.initial_width_hz": (1e-6, 1e6, 0.1, False, False),
    "fit.min_width_hz": (1e-6, 1e6, 0.01, False, False),
    "fit.max_width_hz": (1e-6, 1e6, 0.1, False, False),
    "fit.max_center_shift_hz": (1e-6, 1e6, 0.1, False, False),
    "fit.max_asymmetry_ratio": (1.001, 10, 0.1, False, False),
    "fit.max_profile_phase_degrees": (0.01, 30, 0.1, False, False),
    "fit.restarts": (1, 1000, 1, False, True),
    "fit.max_nfev": (1, 10000000, 100, False, True),
    "fit.max_peaks": (1, 10000, 1, False, True),
    "baseline.lam": (1e-12, 1e300, 0.1, True, False),
    "baseline.p": (1e-6, 0.999999, 0.001, False, False),
    "baseline.half_window": (1, 100000, 1, False, True),
    "baseline.smooth_half_window": (0, 100000, 1, False, True),
    "baseline.poly_order": (0, 1000, 1, False, True),
    "baseline.max_iter": (1, 100000, 5, False, True),
    "baseline.tol": (1e-12, 1, 0.1, True, False),
}


def adjusted_value(path, value, steps, *, fine=False, coarse=False):
    low, high, step, logarithmic, integer = PARAMETERS[path]
    value = float(str(value).replace(",", "."))
    if not math.isfinite(value):
        raise ValueError("В поле должно быть конечное число.")
    factor = 0.1 if fine else 10 if coarse else 1
    if logarithmic:
        # Equal wheel steps have equal relative size, including when going back down.
        exponent = math.log(max(low, min(high, value))) + steps * math.log1p(step * factor)
        result = math.exp(max(math.log(low), min(math.log(high), exponent)))
    else:
        increment = max(1, round(step * factor)) if integer else step * factor
        result = value + steps * increment
    result = max(low, min(high, result))
    return int(round(result)) if integer else float(f"{result:.10g}")


def suggest_parameters(record, recipe, low, high, *, offset=0):
    """Estimate threshold and resolvable linewidths; this is not a global optimizer."""
    recipe = resolve_spin_baseline(recipe, record, low, high, offset)
    x = np.asarray(record["ppm"], dtype=float)
    y = current_signal(record)
    step = abs(float(np.median(np.diff(x))))
    sf = float(record["spectral_width_hz"]) / (step * len(x))
    a, b = sorted((low - offset, high - offset))
    tolerance = step * 1e-8
    mask = (x >= a - tolerance) & (x <= b + tolerance)
    if np.count_nonzero(mask) < 24 or not np.isfinite(sf) or sf <= 0:
        raise ValueError("Для автонастройки выделите область минимум с 24 точками.")
    order = np.argsort(x[mask])
    x, y = x[mask][order], np.asarray(y)[mask][order]
    if not np.isfinite(y).all():
        raise ValueError("Область содержит нечисловые значения.")
    core = engine()
    corrected, _, _ = core.baseline_correct(
        x, y, core.BaselineSettings(**recipe["baseline_default"])
    )
    count = max(3, min(16, len(y) // 10))
    trend = np.linspace(
        float(np.median(corrected[:count])), float(np.median(corrected[-count:])), len(y)
    )
    detrended = corrected - trend
    noise = noise_sigma(detrended)
    amplitude = float(np.max(np.abs(detrended)))
    if amplitude <= max(1e-12, 6 * noise):
        raise ValueError("Не видно уверенных пиков. Сузьте область или настройте порог вручную.")
    threshold = max(6 * noise, 0.005 * amplitude, 1e-12)
    pos, positive_widths = detect_peaks(corrected, threshold, 1)
    neg, negative_widths = detect_peaks(-corrected, threshold, 1)
    signed = bool(recipe["fit"]["signed"] or len(neg))
    widths = np.r_[positive_widths, negative_widths if signed else []]
    widths = widths[np.isfinite(widths) & (widths >= 1)]
    if not len(widths):
        raise ValueError("Не удалось оценить ширину пиков: проверьте границы области.")
    width_points = float(np.median(widths))
    initial = max(step * sf * 2, width_points * step * sf)
    minimum = max(1e-5, min(step * sf * 0.75, initial / 8))
    maximum = max(initial * 2, min((b - a) * sf * 0.5, float(np.max(widths)) * step * sf * 4))
    result = copy.deepcopy(recipe)
    # Use the estimated threshold as a visible editable number. Manual wheel tuning
    # must not be silently overridden by the existing automatic-threshold checkbox.
    result["auto_threshold"] = False
    detected = len(pos) + (len(neg) if signed else 0)
    result["fit"].update(
        threshold=threshold,
        peak_detection="prominence",
        min_prominence=0.0,
        signed=signed,
        initial_width_hz=initial,
        min_width_hz=minimum,
        max_width_hz=maximum,
        min_separation_points=max(1, min(10, int(width_points / 4))),
        max_peaks=max(int(recipe["fit"]["max_peaks"]), 20, min(10000, detected * 2 + 5)),
    )
    result["baseline_default"].update(
        half_window=max(1, min((len(x) - 1) // 2, int(np.ceil(width_points * 5)))),
        smooth_half_window=max(0, min(3, int(width_points / 10))),
        lam=float(np.clip((width_points * 3) ** 4, 1e4, 1e10)),
    )
    core.Session(
        fit=core.FitSettings(**result["fit"]),
        baseline_default=core.BaselineSettings(**result["baseline_default"]),
    ).validate()
    # Report the actual detector on the final local baseline, not another
    # preliminary algorithm. The fit uses this same path and same parameters.
    final_signal, _, _ = core.baseline_correct(
        x, y, core.BaselineSettings(**result["baseline_default"])
    )
    detected = len(core.pick_peaks(x, final_signal, core.FitSettings(**result["fit"]))[0])
    summary = (
        f"Старт: линий-кандидатов {detected} · шум σ≈{noise:.3g} · порог {threshold:.4g} · "
        f"FWHM {initial:.3g} Гц. Проверьте модель и остаток."
    )
    return result, summary
