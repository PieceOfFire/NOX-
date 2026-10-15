"""Shared catalogue and numerical adapter for NOX's extended baseline menu."""

import numpy as np
from base_line import peak_polarity

QUICK_METHODS = ("Адаптивный arPLS", "Фильтр Уиттакера", "Сглаживающий spline")
MORE_OPTIONS = "More options…"
EXTENDED_METHODS = {
    "asls": "AsLS — асимметричное сглаживание",
    "arpls": "arPLS — робастное сглаживание (Spintegrate)",
    "airpls": "airPLS — итеративное сглаживание",
    "drpls": "drPLS — двойное взвешивание",
    "iasls": "IAsLS — улучшенный AsLS",
    "snip": "SNIP — отсечение пиков",
    "modpoly": "ModPoly — полиномиальный фон",
    "imodpoly": "IModPoly — улучшенный полином",
    "mor": "MOR — морфологический фон",
    "none": "none — без коррекции",
}
FIELDS = {
    "lam": ("Сглаживание λ", 1e6),
    "p": ("Асимметрия p", 0.01),
    "half_window": ("Полуокно, точки", 30),
    "smooth_half_window": ("Сглаживание, полуокно", 3),
    "poly_order": ("Порядок полинома", 3),
    "max_iter": ("Лимит итераций", 50),
    "tol": ("Точность", 0.001),
}
METHOD_FIELDS = {
    "asls": ("lam", "p", "max_iter", "tol"),
    "iasls": ("lam", "p", "max_iter", "tol"),
    "arpls": ("lam", "max_iter", "tol"),
    "airpls": ("lam", "max_iter", "tol"),
    "drpls": ("lam", "max_iter", "tol"),
    "snip": ("half_window", "smooth_half_window"),
    "modpoly": ("poly_order", "max_iter", "tol"),
    "imodpoly": ("poly_order", "max_iter", "tol"),
    "mor": ("half_window",),
    "none": (),
}
PREFIX = "Spintegrate: "


def core_method(method):
    return method[len(PREFIX) :] if isinstance(method, str) and method.startswith(PREFIX) else None


def method_label(method):
    return EXTENDED_METHODS.get(core_method(method), method)


def validate_settings(method, parameters=None):
    from spintegrate.core import BaselineSettings, validate_baseline

    if method not in EXTENDED_METHODS:
        raise ValueError("Неизвестный метод базовой линии Spintegrate.")
    settings = BaselineSettings(method=method, **(parameters or {}))
    integers = ("half_window", "smooth_half_window", "poly_order", "max_iter")
    if any(type(getattr(settings, key)) is not int for key in integers):
        raise ValueError("Окна, порядок полинома и лимит итераций должны быть целыми числами.")
    validate_baseline(settings)
    return settings


def extended_baseline(x, signal, method, parameters=None, *, polarity="auto"):
    """Use the same algorithms as Spintegrate with NOX's peak-polarity convention."""
    from spintegrate.core import baseline_correct

    settings = validate_settings(method, parameters)
    x, values = np.asarray(x, dtype=float), np.asarray(signal, dtype=float)
    if method == "none":
        return np.zeros_like(values), []
    order = np.argsort(x)
    if polarity == 0:
        raise ValueError(
            "Для этих методов выберите положительную или отрицательную полярность пиков."
        )
    sign = peak_polarity(values) if polarity == "auto" else polarity
    if sign == 0:
        raise ValueError(
            "Определены пики обеих полярностей. Выберите полярность явно или используйте адаптивный метод NOX."
        )
    if sign not in (-1, 1):
        raise ValueError("Неизвестная полярность пиков.")
    _, line, notes = baseline_correct(x[order], sign * values[order], settings)
    output = np.empty_like(values)
    output[order] = sign * line
    return output, notes
