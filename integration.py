"""Physical integral areas and optional discrete display values; no Tkinter."""

import numpy as np


def integrate_region(ppm, signal, left, right, *, absolute=False, discrete=False, x_offset=0.0):
    ppm = np.asarray(ppm)
    low, high = sorted((float(left) - x_offset, float(right) - x_offset))
    mask = (ppm >= low) & (ppm <= high)
    if np.count_nonzero(mask) < 2:
        return 0.0
    values = np.asarray(signal)[mask]
    if absolute:
        values = np.abs(values)
    value = np.sum(values) if discrete else np.trapezoid(values, ppm[mask])
    return abs(float(value))
