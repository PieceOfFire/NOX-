"""Inspection of one explicit fitted integral in the NOX interface."""

import tkinter as tk
from tkinter import ttk
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from spin_backend import fit_current, model_component, model_component_areas, model_component_fwhms
from signal_quality import fit_quality


def open_diagnostics(app, record, spin):
    self = app
    record = {**record, "spin": spin}
    window = tk.Toplevel(self)
    window.title(f"Himera · Подгонка {record['name']}")
    window.geometry("980x740")
    window.minsize(700, 550)
    panel = ttk.Frame(window, padding=10)
    panel.pack(fill="both", expand=True)
    names = [m["name"] for m in spin["models"]]
    selection = tk.StringVar(value=names[0])
    row = ttk.Frame(panel)
    row.pack(fill="x")
    ttk.Combobox(row, textvariable=selection, values=names, state="readonly", width=25).pack(
        side="left"
    )
    ttk.Label(
        row,
        text=f"φ₀={spin['phases'][0]:.2f}°, φ₁={spin['phases'][1]:.2f}° · "
        f"порог {spin['threshold']:.5g}",
    ).pack(side="left", padx=14)
    figure = Figure(figsize=(8, 4), dpi=100)
    top, residual = figure.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    canvas = FigureCanvasTkAgg(figure, panel)
    canvas.get_tk_widget().pack(fill="both", expand=True)
    table = ttk.Treeview(
        panel, columns=("center", "amplitude", "width", "area"), show="headings", height=4
    )
    for key, label in (
        ("center", "Центр, ppm"),
        ("amplitude", "Амплитуда"),
        ("width", "FWHM, Гц"),
        ("area", "Полная площадь"),
    ):
        table.heading(key, text=label)
        table.column(key, width=150)
    table.pack(fill="x")
    table.bind("<Control-c>", lambda event: self.copy_treeview_rows(table, context="spintegrate"))
    notes = tk.Text(panel, height=5, wrap="word", relief="flat")
    notes.pack(fill="x", pady=(6, 0))

    def refresh(_event=None):
        model = next(m for m in spin["models"] if m["name"] == selection.get())
        top.clear()
        residual.clear()
        x, y = model["x"], model["corrected"]
        top.plot(x, y, color="#1f77b4", linewidth=0.8, label="После локальной линии")
        top.plot(x, model["fitted"], "--", color="#f28e2b", linewidth=1.2, label="Сумма линий")
        for index in range(len(model["centers"])):
            top.plot(
                x,
                model_component(model, index),
                color="#59a14f",
                alpha=0.65,
                linewidth=0.7,
            )
        residual.plot(x, y - model["fitted"], color="#68707d", linewidth=0.7)
        quality = fit_quality(model)
        residual.axhspan(-2 * quality["noise"], 2 * quality["noise"], alpha=0.12, color="#59a14f")
        residual.axhline(0, color="#b7c1cd", linewidth=0.6)
        residual.set_xlabel("Химический сдвиг, ppm")
        residual.set_ylabel("Остаток")
        top.set_xlim(float(np.max(x)), float(np.min(x)))
        top.legend(loc="best")
        top.grid(alpha=0.2)
        residual.grid(alpha=0.2)
        figure.tight_layout()
        canvas.draw_idle()
        table.delete(*table.get_children())
        fwhms = model_component_fwhms(model)
        for index, (center, amp, width) in enumerate(
            zip(model["centers"], model["amplitudes"], model["widths"])
        ):
            table.insert(
                "",
                "end",
                values=(
                    f"{center:.6f}",
                    f"{amp:.6g}",
                    f"{fwhms[index] * spin['sf_mhz']:.5g}",
                    f"{model_component_areas(model)[index]:.6g}",
                ),
            )
        diagnostic = model["diagnostics"]
        rmse = diagnostic.get("relative_rmse")
        summary = f"RMSE/max: {rmse:.4g}" if rmse is not None else "Пики не найдены"
        summary += f" · вычислений: {diagnostic.get('nfev', 0)}"
        r2 = quality["r_squared"]
        summary += " · R²: —" if r2 is None else f" · R²: {100 * r2:.2f} %"
        ratio = quality["noise_ratio"]
        summary += f"\nШум σ={quality['noise']:.4g}; RMSE={quality['rmse']:.4g}; "
        summary += f"RMSE/σ={ratio:.3g}" if ratio is not None else "RMSE/σ=—"
        if not fit_current(record):
            summary += " · сохранённый результат, не текущая обработка"
        notes.configure(state="normal")
        notes.delete("1.0", "end")
        notes.insert(
            "end", summary + "\n" + "\n".join(quality["warnings"] + model["notes"] + spin["notes"])
        )
        notes.configure(state="disabled")

    selection.trace_add("write", lambda *_: refresh())
    refresh()
