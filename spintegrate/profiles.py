"""Shared line shapes, full signed areas and actual FWHM for mixed models.

For Voigt, width is Lorentzian HWHM and extra_width is Gaussian sigma.
For asymmetric pseudo-Voigt, they are left/right HWHM respectively.
Dispersive area is a symmetric-limit integral: the odd term is not counted.
"""

from functools import lru_cache

import numpy as np
from scipy.optimize import brentq
from scipy.special import voigt_profile, wofz

LINE_SHAPES = (
    "lorentzian",
    "pseudo_voigt",
    "gaussian",
    "voigt",
    "asymmetric_pseudo_voigt",
    "phased_voigt",
)
SHAPE_LABELS = {
    "lorentzian": "Лоренц",
    "pseudo_voigt": "Псевдо-Voigt",
    "gaussian": "Гаусс",
    "voigt": "Voigt",
    "asymmetric_pseudo_voigt": "Асимметричный псевдо-Voigt",
    "phased_voigt": "Voigt с дисперсией",
}


@lru_cache(maxsize=1)
def _voigt_halfwidth_table():
    """Normalize a balance parameter to true HWHM, not an approximate FWHM."""
    balance = np.linspace(0, 1, 2001)
    values = np.ones_like(balance)
    for i, eta in enumerate(balance[1:-1], 1):
        sigma, gamma = (1 - eta) / np.sqrt(2 * np.log(2)), eta
        half = voigt_profile(0, sigma, gamma) / 2
        values[i] = brentq(lambda d: voigt_profile(d, sigma, gamma) - half, 0.1, 3.0)
    return balance, values


def voigt_widths(halfwidth, balance):
    """Return gamma and sigma for a specified total HWHM and shape balance."""
    grid, values = _voigt_halfwidth_table()
    factor = np.asarray(halfwidth) / np.interp(balance, grid, values)
    return factor * balance, factor * (1 - balance) / np.sqrt(2 * np.log(2))


def component_curve(
    x, center, width, amplitude, mixing=1.0, shape="lorentzian", extra_width=None, phase=0.0
):
    d = np.asarray(x) - center
    if shape in ("voigt", "phased_voigt"):
        sigma = extra_width
        norm = voigt_profile(0.0, sigma, width)
        if shape == "voigt":
            return amplitude * voigt_profile(d, sigma, width) / norm
        z = wofz((d + 1j * width) / (sigma * np.sqrt(2))) / (sigma * np.sqrt(2 * np.pi))
        return amplitude * (z.real * np.cos(phase) - z.imag * np.sin(phase)) / norm
    if shape == "asymmetric_pseudo_voigt":
        width = np.where(d < 0, width, extra_width)
    if shape == "lorentzian":
        mixing = 1.0
    elif shape == "gaussian":
        mixing = 0.0
    u2 = (d / width) ** 2
    return amplitude * (mixing / (1 + u2) + (1 - mixing) * np.exp(-np.log(2) * u2))


def component_area(width, amplitude, mixing=1.0, shape="lorentzian", extra_width=None, phase=0.0):
    if shape in ("voigt", "phased_voigt"):
        area = amplitude / voigt_profile(0.0, extra_width, width)
        return area * np.cos(phase) if shape == "phased_voigt" else area
    if shape == "asymmetric_pseudo_voigt":
        width = (width + extra_width) / 2
    if shape == "lorentzian":
        mixing = 1.0
    elif shape == "gaussian":
        mixing = 0.0
    return amplitude * width * (mixing * np.pi + (1 - mixing) * np.sqrt(np.pi / np.log(2)))


def component_fwhm(width, mixing=1.0, shape="lorentzian", extra_width=None):
    """FWHM of the absorptive profile; phase does not redefine linewidth."""
    if shape in ("voigt", "phased_voigt"):
        half = voigt_profile(0.0, extra_width, width) / 2
        upper = 4 * (width + extra_width)
        return 2 * brentq(lambda d: voigt_profile(d, extra_width, width) - half, 0.0, upper)
    return width + extra_width if shape == "asymmetric_pseudo_voigt" else 2 * width


def model_components(x, centers, widths, amplitudes, mixing, shapes, extra_widths, phases):
    return np.column_stack(
        [
            component_curve(x, c, w, a, eta, shape, extra, phase)
            for c, w, a, eta, shape, extra, phase in zip(
                centers, widths, amplitudes, mixing, shapes, extra_widths, phases
            )
        ]
    )


def model_areas(widths, amplitudes, mixing, shapes, extra_widths, phases):
    return np.array(
        [
            component_area(w, a, eta, shape, extra, phase)
            for w, a, eta, shape, extra, phase in zip(
                widths, amplitudes, mixing, shapes, extra_widths, phases
            )
        ]
    )
