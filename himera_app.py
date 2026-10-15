"""NOX interface with Spintegrate as a local processing action, not a mode."""

from __future__ import annotations
import copy
from pathlib import Path
import uuid
import tkinter as tk
from tkinter import messagebox

import numpy as np
from nox_app import SpectrumApp as NoxApp
from spin_backend import (
    SPIN_DISPLAY,
    data_snapshot,
    default_recipe,
    fit_current,
    pack_data,
    pack_spin,
    same_state,
    selected_region_area,
    unpack_data,
    unpack_spin,
)
from spin_groups import group_area
from workflow_tools import WorkflowMixin


class HimeraApp(WorkflowMixin, NoxApp):
    INTEGRATION_DISPLAYS = (*NoxApp.INTEGRATION_DISPLAYS, SPIN_DISPLAY)

    def __init__(self):
        self._embedded_sources = {}
        self._spin_busy = self._spin_selecting = False
        self._spin_subfit = None
        self.spin_settings = None
        self._spin_workspace = None
        super().__init__()
        self.title("Himera · Обработка спектров")
        self.geometry("1150x780")
        controls = self.zoom_button.master
        self.spin_button = tk.Button(controls, text="S", width=3, command=self.open_spin_dialog)
        self.spin_button.pack(side="left", before=self.zoom_button, padx=1)
        self.add_formula_tooltip(
            self.spin_button, "Spintegrate: параметры → выделение области → деконволюция"
        )
        self.bind("<Escape>", self.stop_spin_selection, add="+")
        self.integral_table.bind("<Double-1>", self.edit_spin_integral)

    def edit_spin_integral(self, event):
        comparison = self.comparison_record()
        if comparison:
            column = self.integral_table.identify_column(event.x)
            if not column.startswith("#"):
                return
            index = int(column[1:]) - 2
            items = sorted(comparison.get("integrals", []), key=lambda i: i["number"])
            if 0 <= index < len(items) and items[index].get("spin_fit_id"):
                self.open_spin_diagnostics(items[index]["spin_fit_id"])
                return "break"
            return
        iid = self.integral_table.identify_row(event.y)
        if any(str(i["id"]) == iid and i.get("spin_fit_id") for i in self.integrals):
            self.integral_table.selection_set(iid)
            self.open_spin_diagnostics()
            return "break"

    def undo_last_action(self, event=None):
        if self._spin_workspace is not None:
            return "break"
        return super().undo_last_action(event)

    def redo_last_action(self, event=None):
        if self._spin_workspace is not None:
            return "break"
        return super().redo_last_action(event)

    def build_application_menu(self):
        super().build_application_menu()
        bar = self.nametowidget(self.cget("menu"))
        for i in range(bar.index("end") + 1):
            if bar.type(i) == "cascade" and bar.entrycget(i, "label") == "Обработка сигналов":
                menu = self.nametowidget(bar.entrycget(i, "menu"))
                menu.add_separator()
                menu.add_command(label="Spintegrate — деконволюция…", command=self.open_spin_dialog)
                menu.add_command(
                    label="Интеграл из готовой подгонки…", command=self.arm_fitted_integral
                )
                menu.add_command(
                    label="Spintegrate — компоненты и интегралы…",
                    command=self.open_spin_diagnostics,
                )
        self.install_workflow_menu(bar)

    def open_spin_dialog(self):
        if self._spin_busy or self._spectrum_dialog_open:
            return
        if self.active_spectrum_id not in self.spectra:
            messagebox.showinfo("Spintegrate", "Сначала добавьте спектр.", parent=self)
            return
        try:
            if self._spin_selecting:
                self.stop_spin_selection()
                self.status.set("Spintegrate: выбор области выключен.")
            else:
                self.arm_spin_selection(self.spin_settings or default_recipe())
        except (RuntimeError, ValueError) as error:
            messagebox.showerror("Spintegrate", str(error), parent=self)

    def arm_spin_selection(self, settings):
        self.spin_settings = copy.deepcopy(settings)
        self.spin_settings.update(source="current", regions=[])
        self._spin_selecting = True
        self._spin_subfit = None
        self.integration_mode = self.x_zoom_mode = False
        self.integration_button.configure(relief="raised")
        self.zoom_button.configure(relief="raised")
        self.spin_button.configure(relief="sunken")
        self.restore_interaction_mode()
        self.canvas.get_tk_widget().focus_set()
        self.status.set(
            "Spintegrate: выделите область мышью. Откроется подгонка и разделение на интегралы. Esc — выйти."
        )

    def stop_spin_selection(self, _event=None):
        if self._spin_selecting:
            self._spin_selecting = False
            self._spin_subfit = None
            if hasattr(self, "spin_button"):
                self.spin_button.configure(relief="raised")
            self.restore_interaction_mode()

    def create_span_selector(self):
        super().create_span_selector()
        self.restore_interaction_mode()

    def restore_interaction_mode(self):
        super().restore_interaction_mode()
        if self.span is not None and self._spin_selecting:
            self.span.set_active(not self._spin_busy)

    def toggle_integration(self):
        self.stop_spin_selection()
        super().toggle_integration()

    def toggle_x_zoom(self):
        self.stop_spin_selection()
        super().toggle_x_zoom()

    def on_spectrum_selected(self, event=None):
        self.stop_spin_selection()
        super().on_spectrum_selected(event)

    def open_phase_dialog(self):
        self.stop_spin_selection()
        super().open_phase_dialog()

    def open_correction_dialog(self):
        self.stop_spin_selection()
        super().open_correction_dialog()

    def on_x_selected(self, xmin, xmax):
        if self._spin_selecting:
            if not self._spin_busy and abs(xmax - xmin) > 1e-9:
                if self._spin_subfit:
                    self.add_fitted_subintegral(xmin, xmax)
                else:
                    self.start_spin_region(xmin, xmax)
            return
        super().on_x_selected(xmin, xmax)

    def arm_fitted_integral(self):
        if self._spin_busy:
            return
        comparison = self.comparison_record()
        items = comparison.get("integrals", []) if comparison else self.integrals
        selection = self.integral_table.selection()
        selected = next(
            (i for i in items if str(i["id"]) in selection and i.get("spin_fit_id")), None
        )
        fit_id = (
            selected["spin_fit_id"]
            if selected
            else next((i["spin_fit_id"] for i in reversed(items) if i.get("spin_fit_id")), None)
        )
        if not fit_id:
            messagebox.showinfo("Spintegrate", "Сначала выполните подгонку области.", parent=self)
            return
        self.arm_spin_selection(self.spin_settings or default_recipe())
        self._spin_subfit = fit_id
        self.status.set(
            "Выделите интервал внутри готовой подгонки. Полные площади выбранных линий "
            "дадут новый интеграл без повторной подгонки. Esc — выйти."
        )

    def add_fitted_subintegral(self, left, right):
        self.save_active_spectrum()
        comparison = self.comparison_record()
        ids = comparison["spectrum_ids"] if comparison else [self.active_spectrum_id]
        offsets = comparison.get("offsets", {}) if comparison else {}
        left, right = sorted((float(left), float(right)))
        outputs = {}
        for sid in ids:
            record = self.spectra[sid]
            state = record.get("spin_analyses", {}).get(self._spin_subfit)
            offset = float(offsets.get(sid, offsets.get(str(sid), 0)))
            if state and np.isfinite(
                selected_region_area(record, state, left, right, x_offset=offset)
            ):
                outputs[sid] = state
        if not outputs:
            messagebox.showinfo(
                "Spintegrate",
                "Выберите интервал внутри обработанной области. "
                "Если изменили фазу или линию, сначала повторите подгонку.",
                parent=self,
            )
            return
        self.apply_spin_region(
            outputs,
            dict(
                fit_id=self._spin_subfit,
                left=left,
                right=right,
                comparison_id=comparison["id"] if comparison else None,
            ),
        )

    def start_spin_region(self, left, right):
        if self._spin_busy:
            return False
        self.save_active_spectrum()
        comparison = self.comparison_record()
        ids = comparison["spectrum_ids"] if comparison else [self.active_spectrum_id]
        records = {sid: copy.deepcopy(self.spectra[sid]) for sid in ids if sid in self.spectra}
        if not records:
            return False
        left, right = sorted((float(left), float(right)))
        if not np.isfinite([left, right]).all() or left >= right:
            return False
        offsets = dict(comparison.get("offsets", {})) if comparison else {}
        job = dict(
            fit_id=uuid.uuid4().hex,
            left=left,
            right=right,
            comparison_id=comparison["id"] if comparison else None,
        )
        from spin_workspace import SpinWorkspace

        settings = copy.deepcopy(self.spin_settings or default_recipe())
        # A new region starts with coordination enabled; editing an existing fit
        # retains its saved policy and never silently changes a stored model.
        settings["baseline_policy"] = "nox"
        settings.setdefault("baseline_fallback_method", settings["baseline_default"]["method"])
        SpinWorkspace(self, records, job, settings, offsets)
        return True

    def cancel_spin_analysis(self):
        if self._spin_workspace is not None:
            self._spin_workspace.destroy()
        self.status.set("Spintegrate: отменено, спектр и интегралы не изменены.")

    def capture_records(self, ids):
        self.save_active_spectrum()
        return dict(
            records={sid: copy.deepcopy(self.spectra[sid]) for sid in ids if sid in self.spectra},
            comparisons=copy.deepcopy(self.comparisons),
        )

    def apply_spin_region(self, outputs, job):
        if not outputs:
            return
        previous = self.capture_records(outputs)
        fit_id = job["fit_id"]
        for sid, state in outputs.items():
            record = self.spectra[sid]
            record.setdefault("spin_analyses", {})[fit_id] = state
            record["last_spin_fit"] = fit_id
        comparison = self.comparisons.get(job["comparison_id"])
        if comparison is not None:
            items = comparison.setdefault("integrals", [])
        else:
            items = self.integrals
        groups = job.get("groups")
        if groups is not None:
            group_ids = {g["id"] for g in groups}
            items[:] = [
                i
                for i in items
                if not (
                    i.get("spin_fit_id") == fit_id
                    and i.get("spin_group_id")
                    and i["spin_group_id"] not in group_ids
                )
            ]
        for group in groups if groups is not None else [None]:
            existing = next(
                (
                    i
                    for i in items
                    if group
                    and i.get("spin_fit_id") == fit_id
                    and i.get("spin_group_id") == group["id"]
                ),
                None,
            )
            if existing is None and group and job.get("edit"):
                # Upgrade a former whole-region integral in place, preserving its number/preset links.
                existing = next(
                    (
                        i
                        for i in items
                        if i.get("spin_fit_id") == fit_id
                        and not i.get("spin_group_id")
                        and np.allclose(
                            [i["left"], i["right"]], [job["left"], job["right"]], atol=1e-8, rtol=0
                        )
                    ),
                    None,
                )
            if existing is None:
                if comparison is not None:
                    iid = int(comparison.get("next_integral_id", 1))
                    comparison["next_integral_id"] = iid + 1
                else:
                    iid = self._next_integral_id
                    self._next_integral_id += 1
                number = max((int(i.get("number", 0)) for i in items), default=0) + 1
                existing = dict(
                    id=iid,
                    number=number,
                    color=self.integral_colors[(iid - 1) % len(self.integral_colors)],
                )
                items.append(existing)
            existing.update(
                left=job["left"],
                right=job["right"],
                spin_fit_id=fit_id,
                name=group["name"] if group else f"Интеграл {existing['number']} · Spintegrate",
            )
            if group:
                existing["spin_group_id"] = group["id"]
        self.save_active_spectrum()
        self._undo_stack.append(("himera_records", "деконволюция выбранной области", previous))
        self._redo_stack.clear()
        self.trim_history()
        self.show_integral_table()
        self.draw(fit_visible_y=True)
        self.refresh_formula_workspace()
        self.status.set(
            f"Spintegrate: сохранены интегралы ({len(groups) if groups is not None else 1}). Спектр и обычные интегралы не изменены. "
            "Можно выделить ещё область; Esc — выйти."
        )

    def current_history_state(self, kind, target):
        if kind == "workflow_records":
            return self.capture_workflow_state(target["records"])
        if kind == "himera_records":
            return self.capture_records(target["records"])
        return super().current_history_state(kind, target)

    def apply_history_state(self, kind, state):
        if kind == "workflow_records":
            for sid, record in state["records"].items():
                self.spectra[sid] = copy.deepcopy(record)
            self.comparisons = copy.deepcopy(state["comparisons"])
            self.current_comparison_id = state.get("comparison_id")
            self._next_comparison_id = state.get("next_comparison_id", self._next_comparison_id)
            self.restore_active_spectrum(self.active_spectrum_id)
            self.restore_formula_state(state["formula"])
            self.refresh_spectrum_library()
            self.draw()
            self.refresh_formula_workspace()
            return True
        if kind == "himera_records":
            for sid, record in state["records"].items():
                self.spectra[sid] = copy.deepcopy(record)
            self.comparisons = copy.deepcopy(state["comparisons"])
            self.restore_active_spectrum(self.active_spectrum_id)
            self.draw()
            self.refresh_formula_workspace()
            return True
        return super().apply_history_state(kind, state)

    def capture_spectrum_state(self):
        state = super().capture_spectrum_state()
        record = self.spectra.get(self.active_spectrum_id)
        if record:
            state["himera"] = dict(
                spin_analyses=copy.deepcopy(record.get("spin_analyses", {})),
                last_spin_fit=record.get("last_spin_fit"),
                baseline_steps=copy.deepcopy(record.get("baseline_steps", [])),
                baseline_source_unknown=record.get("baseline_source_unknown", False),
            )
        return state

    def spectrum_state_changed(self, previous):
        return super().spectrum_state_changed(previous) or not same_state(
            previous.get("himera"), self.capture_spectrum_state().get("himera")
        )

    def restore_spectrum_state(self, state):
        super().restore_spectrum_state(state)
        record = self.spectra.get(self.active_spectrum_id)
        if record and "himera" in state:
            record.update(copy.deepcopy(state["himera"]))
        self.save_active_spectrum()

    def record_integral_area(self, record, left, right, *, x_offset=0, integral=None):
        fit_id = integral.get("spin_fit_id") if integral else None
        if fit_id:
            state = record.get("spin_analyses", {}).get(fit_id)
            if state is None:
                # Mixed modes from the previous beta retain their ordinary rows.
                if fit_id == "legacy-beta":
                    return super().record_integral_area(
                        record, left, right, x_offset=x_offset, integral=integral
                    )
                return float("nan")
            if integral.get("spin_group_id"):
                return group_area(
                    record,
                    state,
                    integral["spin_group_id"],
                    absolute=self.integration_method.get() == "Сумма модулей относительно 0",
                )
            return selected_region_area(
                record,
                state,
                left,
                right,
                x_offset=x_offset,
                absolute=self.integration_method.get() == "Сумма модулей относительно 0",
            )
        return super().record_integral_area(
            record, left, right, x_offset=x_offset, integral=integral
        )

    def record_integral_display_value(self, record, left, right, *, x_offset=0, integral=None):
        if integral and integral.get("spin_fit_id"):
            value = self.record_integral_area(
                record, left, right, x_offset=x_offset, integral=integral
            )
            if self.integration_display.get() == SPIN_DISPLAY:
                return value / np.pi
            if self.integration_display.get() == self.INTEGRATION_DISPLAYS[1]:
                return value / abs(float(np.median(np.diff(record["ppm"]))))
            return value
        return super().record_integral_display_value(
            record, left, right, x_offset=x_offset, integral=integral
        )

    def integral_display_value(self, item):
        if self.active_spectrum_id in self.spectra:
            return self.record_integral_display_value(
                self.spectra[self.active_spectrum_id], item["left"], item["right"], integral=item
            )
        return super().integral_display_value(item)

    def configure_normal_integral_table(self):
        super().configure_normal_integral_table()
        if self.integration_display.get() == SPIN_DISPLAY:
            self.integral_table.heading("area", text="Обычная площадь / модельная площадь ÷ π")

    def update_integral_table(self, ordered):
        super().update_integral_table(ordered)
        for iid in self.integral_table.get_children():
            values = list(self.integral_table.item(iid, "values"))
            if len(values) == 5 and values[3] in ("nan", "inf", "-inf"):
                values[3:5] = ["—", "—"]
                self.integral_table.item(iid, values=values)

    def update_comparison_integral_table(self, comparison):
        super().update_comparison_integral_table(comparison)
        for iid in self.integral_table.get_children():
            values = self.integral_table.item(iid, "values")
            self.integral_table.item(
                iid, values=["—" if str(v) in ("nan", "inf", "-inf") else v for v in values]
            )

    def relevant_fit_ids(self, record):
        items = record.get("integrals", [])
        comparison = self.comparison_record()
        if comparison and record["id"] in comparison["spectrum_ids"]:
            items = comparison.get("integrals", [])
        return {i["spin_fit_id"] for i in items if i.get("spin_fit_id")}

    def draw_integrals(self):
        # Group integrals share a fit domain; do not pile identical shaded rectangles/labels.
        items = self.integrals
        self.integrals = [i for i in items if not i.get("spin_group_id")]
        try:
            super().draw_integrals()
        finally:
            self.integrals = items
        self.draw_spin_domains(self.axes, items)
        self.update_integral_table(self.ordered_integrals())

    @staticmethod
    def draw_spin_domains(axis, items):
        seen = set()
        for item in items:
            if item.get("spin_group_id") and item["spin_fit_id"] not in seen:
                seen.add(item["spin_fit_id"])
                axis.axvspan(item["left"], item["right"], color="#4c78a8", alpha=0.06)
                for boundary in (item["left"], item["right"]):
                    axis.axvline(boundary, color="#94a3b8", lw=0.8)

    def draw_comparison_integrals(self, axis, comparison):
        items = comparison.get("integrals", [])
        super().draw_comparison_integrals(
            axis, {**comparison, "integrals": [i for i in items if not i.get("spin_group_id")]}
        )
        self.draw_spin_domains(axis, items)

    def draw_spin_groups(self, axes, record, state, fit_id, offset=0, transform=None):
        comparison = self.comparison_record()
        items = comparison.get("integrals", []) if comparison else self.integrals
        model = state["models"][0]
        delta = record.get("ppm_shift", 0) - state["expected"].get("ppm_shift", 0)
        for group in state.get("groups", []):
            item = next(
                (
                    i
                    for i in items
                    if i.get("spin_fit_id") == fit_id and i.get("spin_group_id") == group["id"]
                ),
                None,
            )
            if item is None or not group["components"]:
                continue
            x = model["x"] + delta + offset
            from spin_backend import model_component

            curve = sum(
                (model_component(model, i) for i in group["components"]),
                np.zeros(len(x)),
            )
            if transform:
                curve = transform(curve)
            axes.plot(
                x,
                curve,
                color=item["color"],
                lw=1.2,
                linestyle="--",
                alpha=0.85,
                label=f"I{item['number']} · {item['name']}" if not comparison else None,
            )
            index = int(np.argmax(np.abs(curve - np.median(curve))))
            axes.annotate(
                f"I{item['number']}",
                (x[index], curve[index]),
                xytext=(4, 6),
                textcoords="offset points",
                color=item["color"],
                fontsize=9,
            )

    def fit_visible_plot_y(self):
        """Fit data-coordinate curves in the current X view, not the whole spectrum.

        Domain markers use mixed axes/data coordinates and must not affect Y.
        Stacked comparisons have an intentional fixed layout and keep their scale.
        """
        comparison = self.comparison_record()
        if comparison and comparison.get("mode") == "separate":
            return
        low, high = sorted(self.axes.get_xlim())
        values = []
        for artist in self.axes.lines:
            if not artist.get_visible() or artist.get_transform() is not self.axes.transData:
                continue
            x = np.asarray(artist.get_xdata(), dtype=float)
            y = np.asarray(artist.get_ydata(), dtype=float)
            if x.size < 3 or x.shape != y.shape:
                continue
            mask = (x >= low) & (x <= high) & np.isfinite(x) & np.isfinite(y)
            if np.any(mask):
                values.append(y[mask])
        if not values:
            return
        minimum = min(float(np.min(y)) for y in values)
        maximum = max(float(np.max(y)) for y in values)
        margin = 0.06 * (maximum - minimum)
        if margin <= 0:
            margin = max(abs(maximum) * 0.06, 1.0)
        self.axes.set_ylim(minimum - margin, maximum + margin)
        self.canvas.draw_idle()

    def draw(self, *args, fit_visible_y=False, **kwargs):
        super().draw(*args, **kwargs)
        if self.current_comparison_id is None and not kwargs.get("preview", False):
            record = self.spectra.get(self.active_spectrum_id, {})
            shown = False
            background_shown = False
            for fit_id in sorted(self.relevant_fit_ids(record)) if record else []:
                state = record.get("spin_analyses", {}).get(fit_id)
                if state and fit_current({**record, "spin": state}):
                    delta = record.get("ppm_shift", 0) - state["expected"].get("ppm_shift", 0)
                    for model in state["models"]:
                        baseline = 0 if fit_id == "legacy-beta" else model["baseline"]
                        self.axes.plot(
                            model["x"] + delta,
                            model["fitted"] + baseline,
                            "--",
                            color="#f28e2b",
                            linewidth=1.15,
                            label="Подгонка Spintegrate" if not shown else None,
                        )
                        if fit_id != "legacy-beta" and np.any(model["baseline"]):
                            self.axes.plot(
                                model["x"] + delta,
                                model["baseline"],
                                ":",
                                color="#68707d",
                                linewidth=1,
                                label="Локальный фон Spintegrate" if not background_shown else None,
                            )
                            background_shown = True
                        shown = True
                    self.draw_spin_groups(self.axes, record, state, fit_id)
            if shown:
                self.axes.legend(loc="best")
                self.canvas.draw_idle()
        if fit_visible_y and not kwargs.get("preview", False):
            self.fit_visible_plot_y()
        if not kwargs.get("preview", False):
            self.refresh_calculations()

    def draw_comparison(self, **kwargs):
        super().draw_comparison(**kwargs)
        comparison = self.comparison_record()
        if comparison:
            ids = comparison["spectrum_ids"]
            colors = ("#1f77b4", "#e45756", "#59a14f", "#f28e2b", "#9467bd", "#17becf")
            for index, sid in enumerate(ids):
                record = self.spectra[sid]
                offsets = comparison.get("offsets", {})
                offset = float(offsets.get(sid, offsets.get(str(sid), 0)))
                signal = self.record_signal(record)
                median = float(np.median(signal))
                scale = min(
                    0.78 / max(float(np.max(signal) - median), np.finfo(float).eps),
                    0.48 / max(float(median - np.min(signal)), np.finfo(float).eps),
                )
                level = 1 + (len(ids) - 1 - index) * 1.42
                for fit_id in self.relevant_fit_ids(record):
                    state = record.get("spin_analyses", {}).get(fit_id)
                    if state and fit_current({**record, "spin": state}):
                        delta = record.get("ppm_shift", 0) - state["expected"].get("ppm_shift", 0)
                        for model in state["models"]:
                            curve = model["fitted"] + (
                                0 if fit_id == "legacy-beta" else model["baseline"]
                            )
                            if comparison.get("mode") == "separate":
                                curve = (curve - median) * scale + level
                            self.axes.plot(
                                model["x"] + offset + delta,
                                curve,
                                "--",
                                color=colors[index % len(colors)],
                                linewidth=1.2,
                            )
                            if fit_id != "legacy-beta" and np.any(model["baseline"]):
                                background = model["baseline"]
                                if comparison.get("mode") == "separate":
                                    background = (background - median) * scale + level
                                self.axes.plot(
                                    model["x"] + offset + delta,
                                    background,
                                    ":",
                                    color=colors[index % len(colors)],
                                    alpha=0.55,
                                    linewidth=1,
                                )
                        self.draw_spin_groups(
                            self.axes,
                            record,
                            state,
                            fit_id,
                            offset,
                            (lambda y: y * scale + level)
                            if comparison.get("mode") == "separate"
                            else None,
                        )
            self.canvas.draw_idle()
        self.refresh_calculations()

    def prepare_project_load(self, project):
        saved = project.get("spin_settings")
        self.spin_settings = copy.deepcopy(saved) if isinstance(saved, dict) else None
        if self.spin_settings:
            self.spin_settings.update(source="current", regions=[])
        self._embedded_sources = {
            str(Path(p["source_folder"]).resolve()): p
            for p in project.get("spectra", [])
            if isinstance(p, dict) and isinstance(p.get("himera"), dict)
        }

    def project_source_available(self, source):
        return str(source.resolve()) in self._embedded_sources or super().project_source_available(
            source
        )

    def add_spectrum_folder(self, folder, **kwargs):
        if self._spin_busy:
            return False
        payload = self._embedded_sources.get(str(Path(folder).resolve()))
        if payload is None:
            return super().add_spectrum_folder(folder, **kwargs)
        data = unpack_data(payload["himera"]["data"])
        if kwargs.get("clear_formulas"):
            self.clear_formula_workspace()
        sid = self._next_spectrum_id
        self._next_spectrum_id += 1
        self.spectra[sid] = dict(
            id=sid,
            name=payload.get("name") or Path(folder).name,
            source_folder=Path(folder).resolve(),
            **data,
            integrals=[],
            reference_integral_id=None,
            next_integral_id=1,
            integration_method="Обычная площадь",
            integration_display="Площадь",
        )
        self.activate_spectrum(sid)
        return True

    def open_project(self):
        if self._spin_busy:
            return
        self.stop_spin_selection()
        try:
            super().open_project()
            # The first beta applied its fit to all integral rows. Preserve these
            # explicitly when reading it, without resurrecting global modes.
            for comparison in self.comparisons.values():
                if any(
                    "legacy-beta" in self.spectra[s].get("spin_analyses", {})
                    for s in comparison["spectrum_ids"]
                ):
                    for item in comparison.get("integrals", []):
                        item.setdefault("spin_fit_id", "legacy-beta")
            self.draw()
        finally:
            self._embedded_sources.clear()

    def project_spectrum_data(self, record):
        payload = super().project_spectrum_data(record)
        markers = {int(i["id"]): i for i in record.get("integrals", [])}
        for item in payload["integrals"]:
            for key in ("spin_fit_id", "spin_group_id"):
                if markers.get(item["id"], {}).get(key):
                    item[key] = markers[item["id"]][key]
        payload["himera"] = dict(
            version=2,
            data=pack_data(data_snapshot(record)),
            spin_analyses={key: pack_spin(s) for key, s in record.get("spin_analyses", {}).items()},
            last_spin_fit=record.get("last_spin_fit"),
            baseline_steps=copy.deepcopy(record.get("baseline_steps", [])),
            baseline_source_unknown=record.get("baseline_source_unknown", False),
        )
        return payload

    def project_data(self):
        payload = super().project_data()
        payload.update(application="himera", version=3, spin_settings=self.spin_settings)
        return payload

    def restore_project_integrals(self, raw, reference):
        super().restore_project_integrals(raw, reference)
        tags = {int(i["id"]): i for i in raw or [] if isinstance(i, dict) and "id" in i}
        for item in self.integrals:
            for key in ("spin_fit_id", "spin_group_id"):
                value = tags.get(item["id"], {}).get(key)
                if isinstance(value, str):
                    item[key] = value

    def restore_project_spectrum_payload(self, payload):
        extra = payload.get("himera")
        if not isinstance(extra, dict):
            return super().restore_project_spectrum_payload(payload)
        if extra.get("version") not in (1, 2):
            raise ValueError("Неподдерживаемая версия данных Himera.")
        record = self.spectra[self.active_spectrum_id]
        record.update(unpack_data(extra["data"]))
        record["spin_analyses"] = {
            key: unpack_spin(s) for key, s in extra.get("spin_analyses", {}).items()
        }
        if (
            extra.get("version") == 1
            and extra.get("work_mode") == "spintegrate"
            and extra.get("spin")
        ):
            record["spin_analyses"]["legacy-beta"] = unpack_spin(extra["spin"])
        record["last_spin_fit"] = extra.get("last_spin_fit")
        record["baseline_steps"] = copy.deepcopy(extra.get("baseline_steps", []))
        record["baseline_source_unknown"] = extra.get("baseline_source_unknown", False)
        record["integration_method"] = self.normalize_integration_method(
            payload.get("integration_method", "Обычная площадь")
        )
        record["integration_display"] = payload.get("integration_display", "Площадь")
        self.restore_active_spectrum(record["id"])
        self.restore_project_integrals(
            payload.get("integrals"), payload.get("reference_integral_id")
        )
        if "legacy-beta" in record["spin_analyses"]:
            for item in self.integrals:
                item.setdefault("spin_fit_id", "legacy-beta")
        self.save_active_spectrum()

    def open_spin_diagnostics(self, fit_id=None):
        if self._spin_busy or self._spectrum_dialog_open:
            return
        self.save_active_spectrum()
        record = self.spectra.get(self.active_spectrum_id, {})
        available = self.relevant_fit_ids(record) if record else set()
        selection = self.integral_table.selection()
        comparison = self.comparison_record()
        items = comparison.get("integrals", []) if comparison else record.get("integrals", [])
        chosen = next(
            (i for i in items if str(i["id"]) in selection and i.get("spin_fit_id")),
            None,
        )
        fit_id = fit_id or (chosen["spin_fit_id"] if chosen else record.get("last_spin_fit"))
        if fit_id not in available:
            fit_id = next(iter(sorted(available)), None)
        state = record.get("spin_analyses", {}).get(fit_id)
        if state is None:
            messagebox.showinfo(
                "Spintegrate", "Сначала обработайте выбранную область.", parent=self
            )
            return
        if len(state["models"]) != 1:
            messagebox.showinfo(
                "Spintegrate",
                "Для старой многообластной модели выделите нужную область заново кнопкой S.",
                parent=self,
            )
            return
        from spin_workspace import SpinWorkspace

        ids = comparison["spectrum_ids"] if comparison else [self.active_spectrum_id]
        records = {sid: self.spectra[sid] for sid in ids}
        initial = {
            sid: r["spin_analyses"][fit_id]
            for sid, r in records.items()
            if fit_id in r.get("spin_analyses", {})
        }
        initial = copy.deepcopy(initial)
        group_ids = {
            i["spin_group_id"]
            for i in items
            if i.get("spin_fit_id") == fit_id and i.get("spin_group_id")
        }
        group_names = {
            i["spin_group_id"]: i["name"]
            for i in items
            if i.get("spin_fit_id") == fit_id and i.get("spin_group_id")
        }
        for sid, saved in initial.items():
            if "groups" in saved:
                saved["groups"] = [g for g in saved["groups"] if g["id"] in group_ids]
                for group in saved["groups"]:
                    group["name"] = group_names.get(group["id"], group["name"])
            if fit_current({**records[sid], "spin": saved}):
                shift = records[sid].get("ppm_shift", 0) - saved["expected"].get("ppm_shift", 0)
                for model in saved["models"]:
                    for key in ("centers", "x", "low", "high", "picked_centers"):
                        if key in model:
                            model[key] = model[key] + shift
                saved["expected"] = data_snapshot(records[sid])
                saved["input_snapshot"] = data_snapshot(records[sid])
        offsets = comparison.get("offsets", {}) if comparison else {}
        offset = float(offsets.get(record["id"], offsets.get(str(record["id"]), 0)))
        delta = record.get("ppm_shift", 0) - state["expected"].get("ppm_shift", 0)
        job = dict(
            fit_id=fit_id,
            edit=True,
            comparison_id=comparison["id"] if comparison else None,
            left=state["models"][0]["low"] + offset + delta,
            right=state["models"][0]["high"] + offset + delta,
        )
        window = SpinWorkspace(self, records, job, state["recipe"], offsets, initial)
        if len(initial) != len(records) or not all(
            fit_current({**records[s], "spin": st}) for s, st in initial.items()
        ):
            window.parameters_changed()
        return window

    def open_formula_editor(self):
        editor = super().open_formula_editor()
        if editor is not None:
            editor.title("Himera · Редактор формул NOX")
        return editor
