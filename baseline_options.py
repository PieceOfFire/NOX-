"""Inline controls for extended baseline methods in the correction dialog."""

import tkinter as tk
from tkinter import ttk

from baseline_methods import (
    FIELDS,
    METHOD_FIELDS,
    core_method,
    validate_settings,
)


class BaselineParameters(ttk.Frame):
    def __init__(self, parent, parameters, on_change):
        super().__init__(parent)
        self.on_change = on_change
        self.previous_parameters = dict(parameters)
        self.method = None
        self.fields = {}
        self.field_widgets = {}
        for row, (key, (label, default)) in enumerate(FIELDS.items()):
            value = parameters.get(key, default)
            self.fields[key] = tk.StringVar(value=f"{value:g}")
            name = ttk.Label(self, text=label)
            name.grid(row=row, column=0, sticky="w", padx=(0, 20), pady=3)
            entry = ttk.Entry(self, textvariable=self.fields[key], width=19)
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            self.field_widgets[key] = (name, entry)
            entry.bind("<MouseWheel>", lambda e, k=key: self.nudge(e, k))
            entry.bind("<Up>", lambda e, k=key: self.nudge(e, k, 1))
            entry.bind("<Down>", lambda e, k=key: self.nudge(e, k, -1))
            self.fields[key].trace_add("write", lambda *_: self.on_change())
        self.error = tk.StringVar()
        ttk.Label(self, textvariable=self.error, foreground="#a4262c", wraplength=400).grid(
            row=len(FIELDS), column=0, columnspan=2, sticky="w"
        )
        self.columnconfigure(1, weight=1)

    def nudge(self, event, key, direction=None):
        from spin_tuning import adjusted_value

        steps = direction if direction is not None else int(event.delta / 120)
        if steps:
            try:
                value = adjusted_value(
                    "baseline." + key,
                    self.fields[key].get(),
                    steps,
                    fine=bool(event.state & 1),
                    coarse=bool(event.state & 4),
                )
                self.fields[key].set(f"{value:g}")
                self.error.set("")
            except (ValueError, OverflowError):
                self.error.set("Сначала введите корректное число.")
        return "break"

    def set_method(self, method):
        self.method = method
        key = core_method(method)
        active = METHOD_FIELDS.get(key, ())
        for field, widgets in self.field_widgets.items():
            for widget in widgets:
                widget.grid() if field in active else widget.grid_remove()
        self.error.set("")

    def collect(self):
        key = core_method(self.method)
        parameters = dict(self.previous_parameters)
        if key is not None:
            from dataclasses import asdict

            for field in METHOD_FIELDS[key]:
                try:
                    number = float(self.fields[field].get().strip().replace(",", "."))
                    if field in ("half_window", "smooth_half_window", "poly_order", "max_iter"):
                        if number != int(number):
                            raise ValueError()
                        number = int(number)
                    parameters[field] = number
                except (ValueError, OverflowError):
                    raise ValueError(f"«{FIELDS[field][0]}»: проверьте число.") from None
            settings = asdict(validate_settings(key, parameters))
            settings.pop("method")
            parameters = settings
        self.previous_parameters = dict(parameters)
        return parameters
