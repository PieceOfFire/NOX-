"""Bounded mixed-profile fitting; the legacy analytic solver remains unchanged."""

import numpy as np
from scipy.optimize import least_squares, linear_sum_assignment

from .profiles import model_components, voigt_widths


def resolve_shapes(centers, settings, spacing):
    shapes = [settings.line_shape] * len(centers)
    overrides = settings.component_shapes
    if overrides and len(centers):
        anchors = np.array([item["center"] for item in overrides])
        tolerance = np.array([item.get("tolerance", max(2 * spacing, 0.005)) for item in overrides])
        distances = np.abs(anchors[:, None] - centers)
        rows, columns = linear_sum_assignment(
            np.where(distances <= tolerance[:, None], distances, 1e6 + distances)
        )
        for row, col in zip(rows, columns):
            if distances[row, col] <= tolerance[row]:
                shapes[col] = overrides[row]["shape"]
    return shapes


def fit_profiles(x, corrected, centers, picked, spectrum, session):
    from .core import FitResult, cancellation_diagnostics, r_squared

    f = session.fit
    n = len(centers)
    spacing = float(np.median(np.diff(x)))
    shapes = resolve_shapes(centers, f, spacing)
    scale = max(float(np.max(np.abs(corrected))), np.finfo(float).tiny)
    target = corrected / scale
    lower_w = max(f.min_width_hz / (2 * spectrum.sf_mhz), spacing * 0.25)
    upper_w = f.max_width_hz / (2 * spectrum.sf_mhz)
    if upper_w <= lower_w:
        raise ValueError("Максимальная ширина меньше цифрового разрешения спектра.")
    width0 = max(session.processing.lb_hz, f.initial_width_hz) / (2 * spectrum.sf_mhz)
    width0 = np.clip(width0, lower_w * 1.01, upper_w * 0.99)
    shift_scale = f.max_center_shift_hz / spectrum.sf_mhz
    center_low, center_high = (
        np.maximum(centers - shift_scale, x[0]),
        np.minimum(centers + shift_scale, x[-1]),
    )
    if n > 1:
        mid = (centers[:-1] + centers[1:]) / 2
        center_low[1:] = np.maximum(center_low[1:], mid + spacing * 0.01)
        center_high[:-1] = np.minimum(center_high[:-1], mid - spacing * 0.01)
    mix_ids = np.array(
        [i for i, s in enumerate(shapes) if s not in ("lorentzian", "gaussian")], dtype=int
    )
    asym_ids = np.array(
        [i for i, s in enumerate(shapes) if s == "asymmetric_pseudo_voigt"], dtype=int
    )
    voigt_ids = np.array(
        [i for i, s in enumerate(shapes) if s in ("voigt", "phased_voigt")], dtype=int
    )
    phased_ids = np.array([i for i, s in enumerate(shapes) if s == "phased_voigt"], dtype=int)
    lower = np.r_[np.full(n, -np.inf if f.signed else 0.0), np.full(n, np.log(lower_w))]
    upper = np.r_[np.full(n, np.inf), np.full(n, np.log(upper_w))]
    if f.refine_centers:
        lower = np.r_[lower, (center_low - centers) / shift_scale]
        upper = np.r_[upper, (center_high - centers) / shift_scale]
    lower, upper = (
        np.r_[lower, np.full(len(mix_ids), 1e-6)],
        np.r_[upper, np.full(len(mix_ids), 1 - 1e-6)],
    )
    ratio_bound = np.log(f.max_asymmetry_ratio)
    lower, upper = (
        np.r_[lower, np.full(len(asym_ids), -ratio_bound)],
        np.r_[upper, np.full(len(asym_ids), ratio_bound)],
    )
    if len(phased_ids):
        lower, upper = np.r_[lower, -1.0], np.r_[upper, 1.0]
    if len(x) <= len(lower):
        raise ValueError(f"Недостаточно точек для {len(lower)} параметров гибкой модели.")

    def unpack(z):
        amp, halfwidth = z[:n], np.exp(z[n : 2 * n])
        position = 2 * n
        fitted_centers = centers
        if f.refine_centers:
            fitted_centers = centers + z[position : position + n] * shift_scale
            position += n
        mixing = np.array([0.0 if s == "gaussian" else 1.0 for s in shapes])
        mixing[mix_ids] = z[position : position + len(mix_ids)]
        position += len(mix_ids)
        widths, extra = halfwidth.copy(), halfwidth.copy()
        if len(asym_ids):
            ratio = np.exp(z[position : position + len(asym_ids)])
            widths[asym_ids] = 2 * halfwidth[asym_ids] / (1 + ratio)
            extra[asym_ids] = widths[asym_ids] * ratio
            position += len(asym_ids)
        if len(voigt_ids):
            widths[voigt_ids], extra[voigt_ids] = voigt_widths(
                halfwidth[voigt_ids], mixing[voigt_ids]
            )
        phases = np.zeros(n)
        if len(phased_ids):
            phases[phased_ids] = z[position] * np.deg2rad(f.max_profile_phase_degrees)
        return amp, widths, fitted_centers, mixing, extra, phases, halfwidth

    def components(z):
        amp, widths, fitted_centers, mixing, extra, phases, _ = unpack(z)
        return model_components(x, fitted_centers, widths, amp, mixing, shapes, extra, phases)

    candidates = []
    for attempt in range(f.restarts):
        factor = (1.0, 0.5, 2.0, 0.25, 4.0)[attempt % 5]
        start = np.r_[
            picked / scale,
            np.full(n, np.log(np.clip(width0 * factor, lower_w * 1.001, upper_w * 0.999))),
        ]
        if f.refine_centers:
            start = np.r_[start, np.zeros(n)]
        start = np.r_[start, np.full(len(mix_ids), 0.5), np.zeros(len(asym_ids))]
        if len(phased_ids):
            start = np.r_[start, 0.0]
        candidates.append(
            least_squares(
                lambda z: components(z).sum(axis=1) - target,
                start,
                bounds=(lower, upper),
                x_scale="jac",
                max_nfev=f.max_nfev,
                loss=f.loss,
                f_scale=0.03,
                tr_solver="lsmr" if n >= 20 else "exact",
                ftol=1e-9,
                xtol=1e-9,
                gtol=1e-9,
            )
        )
    good = [r for r in candidates if r.success and np.isfinite(r.x).all()]
    result = min(good or candidates, key=lambda r: r.cost)
    amp, widths, fitted_centers, mixing, extra, phases, halfwidth = unpack(result.x)
    curves = components(result.x) * scale
    fitted = curves.sum(axis=1)
    norms = np.linalg.norm(result.jac, axis=0)
    condition = float(np.linalg.cond(result.jac / np.maximum(norms, np.finfo(float).tiny)))
    rmse = float(np.sqrt(np.mean((target - fitted / scale) ** 2)))
    pairs = cancellation_diagnostics(curves, fitted_centers, amp, f.cancellation_warning)
    cancellation = 1 - float(np.sum(np.abs(fitted))) / max(
        float(np.sum(np.abs(curves))), np.finfo(float).tiny
    )
    notes = []
    if not result.success:
        notes.append("ОПТИМИЗАЦИЯ НЕ СОШЛАСЬ: " + str(result.message))
    if condition > 1e6:
        notes.append(f"Параметры плохо различимы: condition={condition:.3g}.")
    if rmse > f.residual_warning:
        notes.append(f"Большой остаток: относительный RMSE={rmse:.3g}.")
    if np.any((halfwidth < lower_w * 1.02) | (halfwidth > upper_w * 0.98)):
        notes.append("Одна или несколько ширин достигли границы ограничений.")
    if f.refine_centers and np.any(
        (fitted_centers <= center_low + spacing * 0.05)
        | (fitted_centers >= center_high - spacing * 0.05)
    ):
        notes.append("Один или несколько центров достигли границы уточнения.")
    if len(asym_ids) and np.any(
        np.maximum(widths[asym_ids] / extra[asym_ids], extra[asym_ids] / widths[asym_ids])
        > f.max_asymmetry_ratio * 0.98
    ):
        notes.append("Асимметрия достигла ограничения: проверьте форму и состав модели.")
    if len(phased_ids):
        notes.append(
            "Voigt с дисперсией: общая малая фаза модели, не дополнительная фаза NOX. Площадь учитывает симметричную часть; нечётные хвосты не добавляются к интегралу."
        )
        if np.max(np.abs(phases)) >= np.deg2rad(f.max_profile_phase_degrees) * 0.98:
            notes.append("Дисперсионная примесь достигла ограничения.")
    if pairs or cancellation >= f.cancellation_warning:
        notes.append("Обнаружена взаимная компенсация противоположных компонент.")
    diagnostics = dict(
        n_peaks=n,
        nfev=int(result.nfev),
        attempts=len(candidates),
        solver_message=str(result.message),
        relative_rmse=rmse,
        condition_number=condition if np.isfinite(condition) else None,
        cancellation_fraction=cancellation,
        cancellation_pairs=pairs,
        initial_width_ppm=float(width0),
        width_bounds_ppm=[lower_w, upper_w],
        line_shape=f.line_shape,
        line_shapes=shapes,
        refined_centers=f.refine_centers,
        center_shifts_hz=((fitted_centers - centers) * spectrum.sf_mhz).tolist(),
        profile_phase_degrees=float(np.rad2deg(phases[phased_ids[0]])) if len(phased_ids) else 0.0,
        r_squared=r_squared(corrected, fitted),
        n_parameters=len(result.x),
    )
    return FitResult(
        "",
        x,
        corrected,
        np.zeros_like(x),
        corrected,
        fitted_centers,
        picked,
        amp * scale,
        widths,
        fitted,
        bool(result.success),
        notes,
        diagnostics,
        mixing.copy(),
        shapes,
        extra.copy(),
        phases.copy(),
    )
