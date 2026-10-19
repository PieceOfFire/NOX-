"""Selection-linked plot/table properties for ordinary and model integrals."""

import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from formula_engine import format_value
from ui_theme import MUTED


class IntegralInspector(ttk.Frame):
    def __init__(self, app):
        super().__init__(app.table_frame, padding=(6, 5), relief="groove", borderwidth=1)
        self.app, self.item_id, self.sid = app, None, None
        self._job, self._key, self._setting = None, None, False
        self._dirty = False
        self.artists = []
        self.title, self.info = tk.StringVar(self), tk.StringVar(self)
        self.name, self.low, self.high = [tk.StringVar(self) for _ in range(3)]
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, textvariable=self.title, font="AppHeadingFont").pack(side="left")
        ttk.Button(row, text="Приблизить", command=self.zoom).pack(side="right")
        self.model_button = ttk.Button(row, text="Изменить подгонку…", command=self.edit_model)
        self.model_button.pack(side="right", padx=4)
        ttk.Button(row, text="Устойчивость…", command=app.open_integral_stability).pack(
            side="right", padx=4
        )
        fields = ttk.Frame(self)
        fields.pack(fill="x", pady=4)
        for column, (label, variable, width) in enumerate(
            (("Имя", self.name, 22), ("От, ppm", self.low, 10), ("До, ppm", self.high, 10))
        ):
            ttk.Label(fields, text=label).grid(row=0, column=2 * column, padx=(0, 4))
            entry = ttk.Entry(fields, textvariable=variable, width=width)
            entry.grid(row=0, column=2 * column + 1, sticky="ew", padx=(0, 10))
            entry.bind("<Return>", lambda _e: self.apply())
            entry.bind("<Escape>", self.revert)
            if column == 0:
                fields.columnconfigure(1, weight=1)
            else:
                setattr(self, "low_entry" if column == 1 else "high_entry", entry)
            variable.trace_add("write", self.changed)
        ttk.Button(fields, text="Применить", command=self.apply).grid(row=0, column=6)
        ttk.Label(self, textvariable=self.info, foreground=MUTED, wraplength=900).pack(anchor="w")
        app.integral_table.bind("<<TreeviewSelect>>", self.table_selected, add="+")
        app.integral_table.bind("<Button-1>", self.table_click, add="+")
        app.integral_table.bind("<Double-1>", self.table_double)
        app.canvas.mpl_connect("button_press_event", self.plot_click)

    def changed(self, *_args):
        if not self._setting:
            self._dirty = True

    def context(self):
        comparison = self.app.comparison_record()
        items = comparison.get("integrals", []) if comparison else self.app.integrals
        item = next((i for i in items if i["id"] == self.item_id), None)
        sid = (
            self.sid
            if comparison and self.sid in comparison["spectrum_ids"]
            else self.app.active_spectrum_id
        )
        return comparison, item, self.app.spectra.get(sid)

    def schedule_refresh(self):
        if self._job is None:
            self._job = self.after_idle(self.refresh)

    def clear_artists(self):
        for artist in self.artists:
            try:
                artist.remove()
            except (ValueError, NotImplementedError):
                pass
        self.artists.clear()

    def refresh(self):
        self._job = None
        app = self.app
        if getattr(app, "_integral_table_refreshing", False):
            return self.schedule_refresh()
        comparison, item, record = self.context()
        context_key = (
            ("comparison", comparison["id"]) if comparison else ("spectrum", app.active_spectrum_id)
        )
        if self._key is not None and self._key[:2] != context_key:
            self.item_id = None
            item = None
        self.clear_artists()
        if item is None or record is None:
            self.pack_forget()
            self._key = None
            app.canvas.draw_idle()
            return
        key = (*context_key, item["id"])
        if key != self._key or not self._dirty:
            self._setting = True
            self.name.set(item.get("name", f"Интеграл {item['number']}"))
            self.low.set(f"{item['left']:.8g}")
            self.high.set(f"{item['right']:.8g}")
            self._setting = False
            self._dirty = False
        self._key = key
        model = bool(item.get("spin_fit_id"))
        for entry in (self.low_entry, self.high_entry):
            entry.configure(state="readonly" if model else "normal")
        self.model_button.configure(state="normal" if model else "disabled")
        offset = 0.0
        if comparison:
            offset = float(
                comparison.get("offsets", {}).get(
                    record["id"], comparison.get("offsets", {}).get(str(record["id"]), 0)
                )
            )
        value = app.record_integral_display_value(
            record, item["left"], item["right"], x_offset=offset, integral=item
        )
        self.title.set(
            f"I{item['number']} · {'Spintegrate' if model else 'Численный'} · {record['name']}"
        )
        self.info.set(
            f"Значение: {format_value(value) if math.isfinite(value) else '— (подгонка недоступна)'}. "
            + (
                "Имя и границы общие для графика сравнения. "
                if comparison
                else "Правки относятся к текущему спектру. "
            )
            + (
                "Модельная площадь меняется через подгонку, не переносом её границ."
                if model
                else "Enter — применить; Esc — сбросить введённые изменения."
            )
        )
        if not self.winfo_manager():
            self.pack(fill="x", before=app.integral_table, pady=(2, 4))
        axes = app._comparison_axes if comparison else [app.axes]
        for axis in axes:
            self.artists.append(
                axis.axvspan(
                    item["left"],
                    item["right"],
                    alpha=0.12,
                    color=item.get("color", "#4c78a8"),
                    zorder=0.5,
                )
            )
            for line in axis.lines:
                if getattr(line, "_integral_item_id", None) == item["id"]:
                    # Separate overlay avoids mutating the model artist's style.
                    (overlay,) = axis.plot(
                        line.get_xdata(),
                        line.get_ydata(),
                        color=item.get("color", "#4c78a8"),
                        lw=2.1,
                        alpha=0.75,
                        label="_selected_integral",
                    )
                    self.artists.append(overlay)
        app.canvas.draw_idle()

    def table_selected(self, _event=None):
        app = self.app
        if getattr(app, "_integral_table_refreshing", False):
            return
        if self._key and self._key[:2] != app._integral_table_context():
            self._key = None
        selection = app.integral_table.selection()
        if selection and not app.comparison_record():
            try:
                self.item_id = int(selection[0])
            except ValueError:
                return
        elif selection and selection[0].startswith("s"):
            self.sid = int(selection[0][1:])
        self.schedule_refresh()

    def table_click(self, event):
        comparison = self.app.comparison_record()
        if comparison:
            row = self.app.integral_table.identify_row(event.y)
            column = self.app.integral_table.identify_column(event.x)
            items = sorted(comparison.get("integrals", []), key=lambda i: i["number"])
            index = int(column[1:]) - 2 if column.startswith("#") else -1
            if row and 0 <= index < len(items):
                if self._key and self._key[:2] != self.app._integral_table_context():
                    self._key = None
                self.item_id, self.sid = items[index]["id"], int(row[1:])
                self.schedule_refresh()

    def table_double(self, event):
        self.table_click(event)
        self.table_selected()
        self.zoom()
        _, item, record = self.context()
        if item and item.get("spin_fit_id"):
            self.app.open_spin_diagnostics(item["spin_fit_id"], spectrum_id=record["id"])
        return "break"

    def select(self, item, *, sid=None, zoom=False):
        comparison = self.app.comparison_record()
        self._key = None
        self.item_id, self.sid = item["id"], sid or self.app.active_spectrum_id
        row = f"s{self.sid}" if comparison else str(item["id"])
        if self.app.integral_table.exists(row):
            self.app.integral_table.selection_set(row)
            self.app.integral_table.see(row)
        self.app.show_integral_table()
        self.refresh()
        if zoom:
            self.zoom()

    def plot_click(self, event):
        app = self.app
        if (
            event.button != 1
            or event.xdata is None
            or app._spectrum_dialog_open
            or app.integration_mode
            or app.x_zoom_mode
            or app._spin_selecting
            or app.integral_boundary_editor.active
        ):
            return
        comparison = app.comparison_record()
        axes = app._comparison_axes if comparison else [app.axes]
        if event.inaxes not in axes:
            return
        items = comparison.get("integrals", []) if comparison else app.integrals
        item = None
        sid = None
        for line in reversed(event.inaxes.lines):
            iid = getattr(line, "_integral_item_id", None)
            if iid is not None and line.contains(event)[0]:
                item = next((i for i in items if i["id"] == iid), None)
                sid = getattr(line, "_integral_spectrum_id", None)
                break
        if item is None:
            candidates = [i for i in items if i["left"] <= event.xdata <= i["right"]]
            ordinary = [i for i in candidates if not i.get("spin_fit_id")]
            item = ordinary[-1] if ordinary else candidates[0] if len(candidates) == 1 else None
        if item:
            self.select(item, sid=sid, zoom=bool(event.dblclick))

    def zoom(self):
        comparison, item, _ = self.context()
        if item is None:
            return
        margin = max((item["right"] - item["left"]) * 0.15, 1e-4)
        axes = self.app._comparison_axes if comparison else [self.app.axes]
        for axis in axes:
            axis.set_xlim(item["right"] + margin, item["left"] - margin)
        self.app.fit_visible_plot_y()
        self.app.canvas.draw_idle()

    def edit_model(self):
        _, item, record = self.context()
        if item and item.get("spin_fit_id"):
            return self.app.open_spin_diagnostics(item["spin_fit_id"], spectrum_id=record["id"])

    def revert(self, _event=None):
        self._dirty = False
        self.refresh()
        return "break"

    def apply(self):
        app = self.app
        comparison, item, _ = self.context()
        if item is None:
            return
        if app._spectrum_dialog_open or app._batch_running:
            self.info.set("Дождитесь окончания обработки или закройте диалог.")
            return
        try:
            name = self.name.get().strip()
            if not name:
                raise ValueError("Введите имя интеграла.")
            low, high = (
                float(self.low.get().replace(",", ".")),
                float(self.high.get().replace(",", ".")),
            )
            if not math.isfinite(low + high) or low >= high:
                raise ValueError("Границы должны быть конечными; «От» меньше «До».")
            if not item.get("spin_fit_id"):
                ids = comparison["spectrum_ids"] if comparison else [app.active_spectrum_id]
                for sid in ids:
                    off = (
                        float(
                            comparison.get("offsets", {}).get(
                                sid, comparison.get("offsets", {}).get(str(sid), 0)
                            )
                        )
                        if comparison
                        else 0
                    )
                    x = np.asarray(app.spectra[sid]["ppm"]) + off
                    if (
                        low < x.min()
                        or high > x.max()
                        or np.count_nonzero((x >= low) & (x <= high)) < 3
                    ):
                        raise ValueError(
                            "Границы должны покрывать хотя бы три точки каждого спектра."
                        )
            if (
                name == item.get("name", f"Интеграл {item['number']}")
                and low == item["left"]
                and high == item["right"]
            ):
                return
            if comparison:
                app.remember_comparison_integrals(comparison, "свойства общего интеграла")
            else:
                before = app.capture_spectrum_state()
            item["name"] = name
            if not item.get("spin_fit_id"):
                item.update(left=low, right=high)
            if not comparison:
                app.remember_spectrum_state(before, "свойства интеграла")
            self._dirty = False
            app.draw()
            app.status.set("Свойства интеграла изменены. Ctrl+Z — отменить.")
        except ValueError as error:
            self.info.set(str(error))

    def destroy(self):
        if self._job is not None:
            self.after_cancel(self._job)
        super().destroy()
