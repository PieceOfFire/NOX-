"""Thin Tk bindings to canonical state, not a second working copy of a spectrum."""

import tkinter as tk

from spectrum_state import SpectrumStore


class ActiveField:
    def __init__(self, key):
        self.key = key

    def __get__(self, owner, cls=None):
        if owner is None:
            return self
        return owner.spectra.current[self.key]

    def __set__(self, owner, value):
        owner.spectra.current[self.key] = value


class SpectrumVariable(tk.StringVar):
    """Tk projection of a record option; .get() always reads canonical state."""

    def __init__(self, app, key):
        self.app, self.key = app, key
        super().__init__(master=app, value=app.spectra.current[key])
        self.trace_add("write", self._write)

    def _write(self, *_):
        self.app.spectra.current[self.key] = super().get()

    def get(self):
        return self.app.spectra.current[self.key]

    def sync(self):
        self.set(self.get())


class ActiveSpectrum:
    source_folder = ActiveField("source_folder")
    ppm = ActiveField("ppm")
    real = ActiveField("real")
    imaginary = ActiveField("imaginary")
    spectral_width_hz = ActiveField("spectral_width_hz")
    ppm_shift = ActiveField("ppm_shift")
    phase0 = ActiveField("phase0")
    phase1 = ActiveField("phase1")
    phase_pivot = ActiveField("phase_pivot")
    phase_reference = ActiveField("phase_reference")
    corrected = ActiveField("corrected")
    integrals = ActiveField("integrals")
    reference_integral_id = ActiveField("reference_integral_id")
    _next_integral_id = ActiveField("next_integral_id")
    _title = ActiveField("title")

    def initialize_spectrum_store(self):
        self.spectra = SpectrumStore()

    @property
    def active_spectrum_id(self):
        return self.spectra.active_id

    @active_spectrum_id.setter
    def active_spectrum_id(self, value):
        if value is not None and value not in self.spectra:
            raise KeyError(f"Unknown spectrum: {value}")
        self.spectra.active_id = value
