"""Diagnostics and sensitivity of quantitative integrals, independent of Tk."""

from __future__ import annotations

import numpy as np

from integration import integrate_region


def noise_sigma(signal):
    """Robust SD from successive differences (white-noise approximation)."""
    values = np.asarray(signal, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 3:
        return 0.0
    differences = np.diff(values)
    return float(np.median(np.abs(differences - np.median(differences))) / 0.953872)


def integral_quality(ppm, signal, left, right, *, absolute=False, delta=0.001):
    x, y = np.asarray(ppm, dtype=float), np.asarray(signal, dtype=float)
    if x.shape != y.shape or x.ndim != 1 or len(x) < 3:
        raise ValueError("Некорректный спектр для диагностики.")
    valid = np.isfinite(x) & np.isfinite(y)
    order = np.argsort(x[valid])
    x, y = x[valid][order], y[valid][order]
    low, high = sorted((float(left), float(right)))
    mask = (x >= low) & (x <= high)
    if np.count_nonzero(mask) < 8:
        raise ValueError("Для диагностики нужно хотя бы 8 точек в интеграле.")
    step = float(np.median(np.diff(x)))
    delta = max(float(delta), step)
    if not np.isfinite(delta) or delta <= 0:
        raise ValueError("Смещение границ должно быть положительным.")
    # Sidebands, not peak minima: this remains an estimate, never an automatic correction.
    band = max(8 * step, (high - low) * 0.15)
    lhs = y[(x >= low - band) & (x < low)]
    rhs = y[(x > high) & (x <= high + band)]
    reliable_edges = min(len(lhs), len(rhs)) >= 8
    if not reliable_edges:
        count = max(3, min(16, np.count_nonzero(mask) // 8))
        lhs, rhs = y[mask][:count], y[mask][-count:]
    edge_low, edge_high = float(np.median(lhs)), float(np.median(rhs))
    noise = noise_sigma(np.concatenate((lhs, rhs)))
    if noise == 0:
        noise = noise_sigma(y[mask])
    baseline = np.interp(x, [low, high], [edge_low, edge_high])
    area = integrate_region(x, y, low, high, absolute=absolute)
    adjusted = integrate_region(x, y - baseline, low, high, absolute=absolute)
    signed = float(np.trapezoid(y[mask], x[mask]))
    total = float(np.trapezoid(np.abs(y[mask]), x[mask]))
    background_area = float(np.trapezoid(baseline[mask], x[mask]))
    variants = []
    for label, a, b in (
        ("Левая граница −δ", low - delta, high),
        ("Левая граница +δ", low + delta, high),
        ("Правая граница −δ", low, high - delta),
        ("Правая граница +δ", low, high + delta),
    ):
        if a < b and x[0] <= a < b <= x[-1]:
            variants.append((label, integrate_region(x, y, a, b, absolute=absolute)))
    denominator = max(area, np.finfo(float).tiny)
    sensitivity = max((abs(v - area) / denominator * 100 for _, v in variants), default=0.0)
    background_pct = abs(background_area) / denominator * 100
    cancellation = max(0.0, (1 - abs(signed) / total) * 100) if total else 0.0
    warnings = []
    if not reliable_edges:
        warnings.append("Нет свободных боковых полос: фон оценён по краям области")
    if background_pct > 5:
        warnings.append("Заметный остаточный фон / хвосты соседних пиков")
    if sensitivity > 5:
        warnings.append("Интеграл чувствителен к границам")
    if not absolute and cancellation > 5:
        warnings.append("Отрицательная часть уменьшает обычный интеграл")
    if noise > 0 and float(np.max(np.abs(y[mask] - baseline[mask]))) < 5 * noise:
        warnings.append("Слабый сигнал относительно шума")
    if total == 0:
        warnings.append("В области нет сигнала")
    return dict(
        area=area,
        noise=noise,
        edge_low=edge_low,
        edge_high=edge_high,
        background_area=background_area,
        background_pct=background_pct,
        edge_adjusted_area=adjusted,
        boundary_pct=sensitivity,
        cancellation_pct=cancellation,
        delta=delta,
        warnings=warnings,
        variants=variants,
        plot_x=x[(x >= low - band) & (x <= high + band)],
        plot_y=y[(x >= low - band) & (x <= high + band)],
        plot_background=baseline[(x >= low - band) & (x <= high + band)],
    )


def fit_quality(model):
    from spintegrate.core import r_squared

    observed = np.asarray(model["corrected"], dtype=float)
    fitted = np.asarray(model["fitted"], dtype=float)
    residual = observed - fitted
    noise = noise_sigma(observed)
    rmse = float(np.sqrt(np.mean(residual**2)))
    scale = float(np.max(np.abs(observed)))
    std = float(np.std(residual))
    autocorrelation = (
        float(np.corrcoef(residual[:-1], residual[1:])[0, 1])
        if len(residual) > 3 and std > max(1e-12, scale * 1e-12)
        else 0.0
    )
    ratio = rmse / noise if noise > max(1e-12, scale * 1e-12) else None
    warnings = []
    if ratio is not None and ratio > 2:
        warnings.append("Остаток выше оценённого шума: проверьте состав / форму линий")
    if abs(autocorrelation) > 0.5 and rmse > max(noise, scale * 1e-4):
        warnings.append("В остатке есть структура, а не только шум")
    if not len(model.get("centers", [])):
        warnings.append("Компоненты не найдены")
    return dict(
        r_squared=r_squared(observed, fitted),
        noise=noise,
        rmse=rmse,
        noise_ratio=ratio,
        autocorrelation=autocorrelation,
        warnings=warnings,
    )
