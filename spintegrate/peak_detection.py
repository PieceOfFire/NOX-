"""One peak detector shared by initial estimates and the actual model.

Smoothing is used only to reject noise candidates; original samples are fitted.
Prominence measures a maximum above its surroundings, not above global zero.
"""

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks, peak_widths


def noise_sigma(y):
    y = np.asarray(y, dtype=float)
    if len(y) < 3:
        return 0.0
    differences = np.diff(y)
    second = np.diff(y, n=2)
    first_sigma = np.median(np.abs(differences - np.median(differences))) / 0.9539
    second_sigma = np.median(np.abs(second - np.median(second))) / 1.65215
    # Differences alone underestimate correlated ripples. A short high-pass
    # residual also measures them; the robust median limits influence of peaks.
    residual = y - gaussian_filter1d(y, 3.0)
    ripple_sigma = np.median(np.abs(residual - np.median(residual))) / 0.67449
    return float(max(min(first_sigma, second_sigma), ripple_sigma))


def detect_peaks(y, threshold, separation=2, *, signed=False, prominence=0.0):
    """Return raw indices and widths of statistically expressed extrema."""
    y = np.asarray(y, dtype=float)
    count = max(3, min(16, len(y) // 10))
    trend = np.linspace(float(np.median(y[:count])), float(np.median(y[-count:])), len(y))
    detrended = y - trend
    smooth = gaussian_filter1d(detrended, 0.8)
    minimum = (
        float(prominence) if prominence > 0 else max(float(threshold), 6 * noise_sigma(detrended))
    )
    candidates, widths = [], []
    for sign in (1.0, -1.0) if signed else (1.0,):
        signal = sign * smooth
        found, _ = find_peaks(
            signal, height=threshold, prominence=minimum, distance=max(1, int(separation))
        )
        for index, width in zip(found, peak_widths(signal, found, rel_height=0.5)[0]):
            # Recover the original extremum (not the blurred grid location).
            a, b = max(0, index - 1), min(len(y), index + 2)
            raw_index = a + int(np.argmax(sign * detrended[a:b]))
            if sign * y[raw_index] > 0:
                candidates.append(raw_index)
                widths.append(width)
    # Recovery can move adjacent candidates closer; enforce separation once more.
    kept = []
    for position in sorted(
        range(len(candidates)), key=lambda p: abs(detrended[candidates[p]]), reverse=True
    ):
        if all(abs(candidates[position] - candidates[other]) >= separation for other in kept):
            kept.append(position)
    kept.sort(key=lambda p: candidates[p])
    return (
        np.array([candidates[i] for i in kept], dtype=int),
        np.array([widths[i] for i in kept], dtype=float),
    )
