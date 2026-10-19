"""User-selected CSV scope, units and Excel-friendly formatting."""

import copy
import tkinter as tk
from tkinter import filedialog, ttk

from integral_csv import DISPLAYS, build_integration_table, write_integration_csv
from ui_theme import MUTED, mark_primary


def export_integrals_csv(app, *, scope="current"):
    if not app.workflow_available():
        return
    window = tk.Toplevel(app)
    window.title("Himera · Экспорт интегралов в CSV")
    window.geometry("640x390")
    window.minsize(570, 350)
    window.transient(app)
    window.grab_set()
    body = ttk.Frame(window, padding=16)
    body.pack(fill="both", expand=True)
    scopes = {"Текущий график": "current", "Все добавленные спектры": "all"}
    if app.batch_session:
        scopes["Очередь ручной серии"] = "batch"
    selected_scope = tk.StringVar(
        window, value=next((k for k, v in scopes.items() if v == scope), "Текущий график")
    )
    display = tk.StringVar(window, value=app.integration_display.get())
    separator = tk.StringVar(window, value="Точка с запятой — Excel")
    decimal = tk.BooleanVar(window, value=True)
    bounds = tk.BooleanVar(window, value=False)
    for row, (label, variable, choices) in enumerate(
        (
            ("Спектры", selected_scope, tuple(scopes)),
            ("Значения", display, DISPLAYS),
            ("Разделитель", separator, ("Точка с запятой — Excel", "Запятая", "Табуляция")),
        )
    ):
        ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=7, padx=(0, 10))
        ttk.Combobox(body, state="readonly", textvariable=variable, values=choices).grid(
            row=row, column=1, sticky="ew", pady=7
        )
    body.columnconfigure(1, weight=1)
    ttk.Checkbutton(body, text="Десятичная запятая (для русского Excel)", variable=decimal).grid(
        row=3, columnspan=2, sticky="w", pady=6
    )
    ttk.Checkbutton(
        body, text="Добавить названия и границы каждого интеграла", variable=bounds
    ).grid(row=4, columnspan=2, sticky="w", pady=6)
    ttk.Label(
        body,
        text="Строки — спектры, столбцы — I1, I2… Недоступные значения остаются пустыми с объяснением в «Примечаниях». UTF-8 с BOM.",
        foreground=MUTED,
        wraplength=580,
    ).grid(row=5, columnspan=2, sticky="w", pady=10)
    status = tk.StringVar(window)
    ttk.Label(body, textvariable=status, wraplength=580).grid(row=6, columnspan=2, sticky="w")
    footer = ttk.Frame(body)
    footer.grid(row=7, columnspan=2, sticky="ew", pady=(10, 0))
    ttk.Button(footer, text="Отмена", command=window.destroy).pack(side="right")

    def save():
        try:
            app.save_active_spectrum()
            chosen = scopes[selected_scope.get()]
            comparison = app.comparison_record() if chosen == "current" else None
            ids = (
                app.batch_session.spectrum_ids
                if chosen == "batch"
                else list(app.spectra)
                if chosen == "all"
                else comparison["spectrum_ids"]
                if comparison
                else [app.active_spectrum_id]
            )
            entries = []
            for sid in ids:
                record = app.spectra[sid]
                if comparison:
                    items, offset = app.integral_context(record)
                else:
                    items, offset = record.get("integrals", []), 0
                entry = dict(
                    record=record,
                    items=items,
                    offset=offset,
                    method=app.integration_method.get()
                    if chosen == "current"
                    else record.get("integration_method", "Обычная площадь"),
                )
                if chosen == "batch":
                    from batch_workflow import STATUS_LABELS

                    entry["status"] = STATUS_LABELS[app.batch_session.item(sid)["status"]]
                entries.append(copy.deepcopy(entry))
            headers, rows, warnings = build_integration_table(
                entries, display=display.get(), include_bounds=bounds.get()
            )
            filename = filedialog.asksaveasfilename(
                parent=window,
                title="Экспорт интегралов",
                defaultextension=".csv",
                filetypes=(("CSV", "*.csv"),),
                initialfile="integrals.csv",
            )
            if not filename:
                return
            delimiter = {"Точка с запятой — Excel": ";", "Запятая": ",", "Табуляция": "\t"}[
                separator.get()
            ]
            write_integration_csv(
                filename, headers, rows, separator=delimiter, decimal_comma=decimal.get()
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            status.set(f"Ошибка: {error}")
            return
        app.status.set(
            f"CSV сохранён: {filename}. Спектров: {len(rows)}; недоступных значений: {warnings}."
        )
        window.destroy()

    mark_primary(ttk.Button(footer, text="Сохранить CSV…", command=save)).pack(side="right", padx=8)
    window.csv_save = save
    window.csv_scope, window.csv_display = selected_scope, display
    return window
