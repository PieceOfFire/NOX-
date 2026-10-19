"""Canonical spectrum data shared by processing, history and numerical workers.

Records remain dict-compatible for project files and existing processing APIs.
Snapshots are detached: undo and worker inputs must never alias live arrays.
"""

from __future__ import annotations

import copy

import numpy as np

from base_line import phase_correct


HISTORY_FIELDS = (
    "phase0",
    "phase1",
    "phase_pivot",
    "phase_reference",
    "ppm",
    "ppm_shift",
    "corrected",
    "integrals",
    "reference_integral_id",
    "next_integral_id",
    "integration_method",
    "integration_display",
    "baseline_steps",
    "baseline_source_unknown",
    "spin_analyses",
    "last_spin_fit",
)


def same_state(a, b):
    """Exact comparison, including nested NumPy arrays, without ambiguous truth values."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and np.array_equal(a, b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_state(a[key], b[key]) for key in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same_state(x, y) for x, y in zip(a, b))
    return a == b


def current_signal(record):
    """The one definition of the working signal; corrected data are not phased twice."""
    if record.get("corrected") is not None:
        return np.asarray(record["corrected"])
    return phase_correct(
        record["real"],
        record["imaginary"],
        record.get("phase0", 0),
        record.get("phase1", 0),
        record.get("phase_pivot", 0.5),
    )


class SpectrumState(dict):
    """One live record, with fresh defaults for optional processing state."""

    def __init__(self, values=None, **kwargs):
        super().__init__(
            source_folder=None,
            ppm=None,
            real=None,
            imaginary=None,
            spectral_width_hz=0.0,
            ppm_shift=0.0,
            phase0=0.0,
            phase1=0.0,
            phase_pivot=0.5,
            phase_reference=None,
            corrected=None,
            title="NMR",
            integrals=[],
            reference_integral_id=None,
            next_integral_id=1,
            integration_method="Обычная площадь",
            integration_display="Площадь",
            baseline_steps=[],
            baseline_source_unknown=False,
            spin_analyses={},
            last_spin_fit=None,
        )
        if values is not None:
            self.update(values)
        self.update(kwargs)

    def snapshot(self):
        return copy.deepcopy({key: self[key] for key in HISTORY_FIELDS})

    def restore(self, snapshot):
        # Preserve raw acquisition data and record identity. Copy the entire
        # processing state together, including fits, groups and baseline history.
        self.update(
            copy.deepcopy({key: snapshot[key] for key in HISTORY_FIELDS if key in snapshot})
        )


class SpectrumStore(dict):
    """Library whose active record is always looked up by ID, never cached."""

    def __init__(self):
        super().__init__()
        self.active_id = None
        self.detached = SpectrumState()

    @property
    def current(self):
        return self[self.active_id] if self.active_id in self else self.detached

    def __setitem__(self, key, value):
        super().__setitem__(
            key, value if isinstance(value, SpectrumState) else SpectrumState(value)
        )

    def update(self, values=(), **kwargs):
        for key, value in dict(values, **kwargs).items():
            self[key] = value

    def __ior__(self, values):
        self.update(values)
        return self

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = {} if default is None else default
        return self[key]

    def clear(self):
        super().clear()
        self.active_id = None
        self.detached = SpectrumState()
