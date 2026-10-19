"""Quality checks, repeatable batch processing and reports for the Himera UI."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from formula_document import FormulaDocument
from formula_engine import FormulaEngine, format_value
from processing_templates import apply_template, load_template, make_template
from report_export import write_report
from sensitivity import analyze_sensitivity, sensitivity_case_count, summarize_sensitivity
from signal_quality import fit_quality, integral_quality
from spin_backend import current_signal, same_state
from view_preferences import capture_table_view, restore_table_view


class ResultsWindow(tk.Toplevel):
    """Common light table window; asynchronous jobs never touch Tk from a worker."""

    def __init__(self, app, title, headers, *, plot=False, modal=False):
        super().__init__(app)
        self.app, self.closed = app, False
        self.stop = threading.Event()
        self.events = queue.Queue()
        self.poll_id = None
        self.running = False
        self.progress_last = (0, 0, "")
        self._table_schema = None
        self.modal = modal
        self.title(title)
        width = min(1120, max(800, self.winfo_screenwidth() - 80))
        height = min(760, max(560, self.winfo_screenheight() - 140))
        self.geometry(f"{width}x{height}")
        self.minsize(760, 520)
        if modal:
            app._spectrum_dialog_open = True
            self.transient(app)
            self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.footer = tk.Frame(self, padx=10, pady=8)
        self.footer.pack(side="bottom", fill="x")
        tk.Button(self.footer, text="Закрыть", command=self.destroy).pack(side="right")
        self.progress_frame = ttk.Frame(self, padding=(10, 4))
        self.progress = ttk.Progressbar(self.progress_frame, mode="determinate", maximum=1)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.stop_button = ttk.Button(
            self.progress_frame, text="Остановить", command=self.request_stop, state="disabled"
        )
        self.stop_button.pack(side="right")
        self.status = tk.StringVar()
        tk.Label(
            self,
            textvariable=self.status,
            wraplength=950,
            justify="left",
            anchor="w",
            padx=10,
            pady=5,
        ).pack(side="bottom", fill="x")
        self.options = tk.Frame(self, padx=10, pady=8)
        self.options.pack(fill="x")
        if plot:
            self.result_split = tk.PanedWindow(
                self, orient="vertical", sashwidth=6, showhandle=True, bd=0
            )
            self.result_split.pack(fill="both", expand=True)
        area = tk.Frame(self.result_split if plot else self, padx=10, pady=5)
        if plot:
            self.result_split.add(area, minsize=120, height=210, stretch="always")
        else:
            area.pack(fill="both", expand=True)
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(
            area,
            columns=tuple(range(len(headers))),
            show="headings",
            selectmode="extended",
            height=8 if plot else 16,
        )
        for i, label in enumerate(headers):
            self.table.heading(i, text=label)
            self.table.column(i, width=145, minwidth=85)
        self.table.grid(row=0, column=0, sticky="nsew")
        for orient, row, column, command in (
            ("vertical", 0, 1, self.table.yview),
            ("horizontal", 1, 0, self.table.xview),
        ):
            scroll = ttk.Scrollbar(area, orient=orient, command=command)
            scroll.grid(row=row, column=column, sticky="ns" if orient == "vertical" else "ew")
            self.table.configure(
                **{("yscrollcommand" if orient == "vertical" else "xscrollcommand"): scroll.set}
            )
        self.table.bind("<Control-KeyPress>", self.copy)
        if plot:
            self.figure = Figure(figsize=(8, 3), dpi=100)
            self.axes = self.figure.subplots()
            plot_frame = ttk.Frame(self.result_split)
            self.result_split.add(plot_frame, minsize=180, stretch="always")
            self.canvas = FigureCanvasTkAgg(self.figure, plot_frame)
            self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=5)

    def copy(self, event):
        if event.keycode == 67 or event.keysym.lower() in ("c", "с"):
            self.app.copy_treeview_rows(self.table, context="анализ")
            return "break"

    def set_rows(self, rows):
        def cell(value):
            if value is None:
                return "—"
            if isinstance(value, (float, np.floating)):
                return format_value(value) if np.isfinite(value) else "—"
            return value

        schema = tuple((str(c), self.table.heading(c, "text")) for c in self.table.cget("columns"))
        state = capture_table_view(self.table) if schema == self._table_schema else {}
        self._table_schema = schema
        self.table.delete(*self.table.get_children())
        for i, row in enumerate(rows):
            self.table.insert(
                "",
                "end",
                iid=str(i),
                values=[cell(v) for v in row],
            )
        restore_table_view(self.table, state)

    def report_progress(self, completed, total, label=""):
        """Worker-safe: all Tk updates happen later in the GUI poll."""
        if not self.closed:
            self.events.put(("progress", (completed, total, str(label))))

    def request_stop(self):
        if self.running:
            self.stop.set()
            self.stop_button.configure(state="disabled")
            self.status.set(
                "Остановка… Текущая подгонка безопасно завершится; готовые результаты сохранятся."
            )

    def start(self, work, done, *, failed=None):
        if self.running or self.closed:
            return False
        self.stop.clear()
        self.running = True
        self.progress_last = (0, 0, "")
        self.progress.configure(maximum=1, value=0)
        self.progress_frame.pack(side="bottom", fill="x", before=self.options)
        self.stop_button.configure(state="normal")
        self.status.set("Вычисление… «Остановить» сохранит готовую часть проверки.")
        self.done = done
        self.failed = failed

        def worker():
            try:
                self.events.put((True, work(self.stop)))
            except Exception as error:
                self.events.put((False, str(error)))

        threading.Thread(target=worker, daemon=True, name="himera-workflow").start()
        self.poll_id = self.after(80, self.poll)
        return True

    def poll(self):
        if self.closed:
            return
        result = None
        latest = None
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                latest = payload
            else:
                result = kind, payload
                break
        if latest:
            completed, total, label = latest
            total = max(0, int(total))
            completed = min(total, max(0, int(completed)))
            self.progress_last = completed, total, label
            self.progress.configure(maximum=max(1, total), value=completed)
            if not self.stop.is_set():
                self.status.set(f"{label} · {completed}/{total}")
        if result is None:
            self.poll_id = self.after(80, self.poll)
            return
        self.poll_id = None
        self.running = False
        self.stop_button.configure(state="disabled")
        success, payload = result
        if success:
            self.done(payload)
            if self.stop.is_set() and not self.status.get().startswith("Остановлено"):
                self.status.set(
                    "Остановлено. Результаты могут быть неполными. " + self.status.get()
                )
        else:
            self.status.set("Ошибка: " + payload)
            if self.failed is not None:
                self.failed(payload)

    def destroy(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        if self.poll_id:
            self.after_cancel(self.poll_id)
        if self.modal:
            self.app._spectrum_dialog_open = False
            self.app.restore_interaction_mode()
        super().destroy()


class WorkflowMixin:
    def install_workflow_menu(self, bar):
        self.last_sensitivity = None
        self._sensitivity_snapshot = None
        for i in range(bar.index("end") + 1):
            if bar.type(i) != "cascade":
                continue
            menu = self.nametowidget(bar.entrycget(i, "menu"))
            label = bar.entrycget(i, "label")
            if label == "Обработка сигналов":
                menu.add_separator()
                menu.add_command(
                    label="Контроль качества интегралов…", command=self.open_integral_quality
                )
                menu.add_command(
                    label="Устойчивость интегралов…", command=self.open_integral_stability
                )
                menu.add_command(
                    label="Шаблоны обработки серии…", command=self.open_processing_templates
                )
            elif label == "Расчёты":
                menu.add_separator()
                menu.add_command(label="Оценка устойчивости…", command=self.open_sensitivity)
            elif label == "Файл":
                menu.add_separator()
                menu.add_command(
                    label="Экспорт интегралов в CSV…", command=self.export_integrals_csv
                )
                menu.add_command(label="Экспорт отчёта…", command=self.export_analysis_report)

    def workflow_available(self):
        if self._spin_busy or self._spectrum_dialog_open:
            return False
        self.save_active_spectrum()
        if self.active_spectrum_id not in self.spectra:
            messagebox.showinfo("Himera", "Сначала добавьте спектр.", parent=self)
            return False
        return True

    def integral_context(self, record, block=None, *, calculation=False):
        stored = block.get("comparison_id") if block else None
        comparison = (
            self.comparisons.get(stored)
            if isinstance(stored, int)
            else self.calculation_comparison_record()
            if calculation
            else self.comparison_record()
        )
        if comparison and record["id"] in comparison["spectrum_ids"]:
            offsets = comparison.get("offsets", {})
            offset = float(offsets.get(record["id"], offsets.get(str(record["id"]), 0)))
            items = comparison.get("integrals", [])
        else:
            offset, items = 0.0, record.get("integrals", [])
        if block is None:
            return items, offset
        number = block.get("area_number")
        item = next((i for i in items if i.get("number") == number), None)
        if item is None and number is None:
            item = next((i for i in items if i.get("id") == block.get("integral_id")), None)
        # Match the main formula resolver: absent comparison input can fall back to a local integral.
        if item is None and comparison:
            item = next((i for i in record.get("integrals", []) if i.get("number") == number), None)
            offset = 0.0
        return item, offset

    def register_baseline_step(self, step):
        record = self.spectra[self.active_spectrum_id]
        record.setdefault("baseline_steps", []).append(copy.deepcopy(step))

    def quality_entries(self, records=None, delta=0.001, *, calculation=False):
        entries = []
        absolute = self.integration_method.get() == "Сумма модулей относительно 0"
        if records is None:
            comparison = self.comparison_record()
            records = (
                [self.spectra[sid] for sid in comparison["spectrum_ids"]]
                if comparison
                else [self.spectra[self.active_spectrum_id]]
            )
        for record in records:
            items, offset = self.integral_context(record, calculation=calculation)
            for item in items:
                try:
                    if item.get("spin_fit_id"):
                        state = record.get("spin_analyses", {}).get(item["spin_fit_id"])
                        if not state:
                            raise ValueError("Подгонка отсутствует")
                        model = state["models"][0]
                        info = fit_quality(model)
                        value = self.record_integral_area(
                            record, item["left"], item["right"], integral=item, x_offset=offset
                        )
                        warnings = info["warnings"] + (
                            [] if np.isfinite(value) else ["Подгонка или состав группы устарели"]
                        )
                        shift = float(record.get("ppm_shift", 0)) - float(
                            state["expected"].get("ppm_shift", 0)
                        )
                        result = dict(
                            area=value,
                            noise=info["noise"],
                            background_pct=None,
                            boundary_pct=None,
                            cancellation_pct=None,
                            warnings=warnings,
                            fit=True,
                            plot_x=model["x"] + shift + offset,
                            plot_y=model["corrected"] - model["fitted"],
                            plot_background=np.zeros(len(model["x"])),
                        )
                    else:
                        result = integral_quality(
                            record["ppm"],
                            current_signal(record),
                            item["left"] - offset,
                            item["right"] - offset,
                            absolute=absolute,
                            delta=delta,
                        )
                        result["plot_x"] += offset
                    entries.append(dict(record=record, item=item, offset=offset, quality=result))
                except (ValueError, RuntimeError) as error:
                    entries.append(
                        dict(
                            record=record,
                            item=item,
                            offset=offset,
                            quality=dict(
                                area=None,
                                noise=None,
                                background_pct=None,
                                boundary_pct=None,
                                cancellation_pct=None,
                                warnings=[str(error)],
                            ),
                        )
                    )
        return entries

    def open_integral_quality(self):
        if not self.workflow_available():
            return
        window = ResultsWindow(
            self,
            "Himera · Контроль качества интегралов",
            (
                "Спектр",
                "Интеграл",
                "Площадь",
                "Шум σ",
                "Фон, %",
                "Границы, %",
                "Вычитание минусов, %",
                "Диагностика",
            ),
            plot=True,
        )
        delta = tk.StringVar(value="0.001")
        tk.Label(window.options, text="Проверить сдвиг границ ±δ, ppm:").pack(side="left")
        ttk.Entry(window.options, textvariable=delta, width=10).pack(side="left", padx=6)

        def refresh():
            try:
                shift = float(delta.get().replace(",", "."))
                if not np.isfinite(shift) or shift <= 0:
                    raise ValueError("δ должно быть положительным")
                window.entries = self.quality_entries(delta=shift)
            except ValueError as error:
                window.status.set(str(error))
                return
            window.set_rows(
                [
                    (
                        e["record"]["name"],
                        f"I{e['item']['number']} · {e['item']['name']}",
                        *[
                            e["quality"].get(k)
                            for k in (
                                "area",
                                "noise",
                                "background_pct",
                                "boundary_pct",
                                "cancellation_pct",
                            )
                        ],
                        "; ".join(e["quality"]["warnings"]) or "Заметных признаков проблемы нет",
                    )
                    for e in window.entries
                ]
            )
            window.status.set(
                "Фон оценён по боковым полосам: они могут содержать соседние пики. Проверка не доказывает правильность фазы. Ctrl+C — копировать."
            )
            if window.entries:
                window.table.selection_set("0")
                plot()

        def plot(_event=None):
            chosen = window.table.selection()
            if not chosen:
                return
            entry = window.entries[int(chosen[0])]
            q = entry["quality"]
            window.status.set(
                f"{entry['record']['name']} · I{entry['item']['number']}: "
                + ("; ".join(q["warnings"]) or "Заметных признаков проблемы нет")
                + "\nФон у краёв может включать хвосты пиков; оценка не вычитается автоматически. Ctrl+C — копировать."
            )
            ax = window.axes
            ax.clear()
            if "plot_x" in q:
                ax.plot(
                    q["plot_x"],
                    q["plot_y"],
                    lw=0.8,
                    label="Остаток подгонки" if q.get("fit") else "Сигнал",
                )
                ax.plot(
                    q["plot_x"],
                    q["plot_background"],
                    "--",
                    lw=1,
                    label="Ноль" if q.get("fit") else "Оценка фона у краёв",
                )
                ax.axhline(0, color="#888", lw=0.6)
                if not q.get("fit"):
                    ax.axvspan(
                        entry["item"]["left"],
                        entry["item"]["right"],
                        alpha=0.1,
                        color=entry["item"]["color"],
                    )
                ax.set_xlim(float(np.max(q["plot_x"])), float(np.min(q["plot_x"])))
                ax.legend(fontsize=8)
            ax.set_xlabel("Химический сдвиг, ppm")
            ax.grid(alpha=0.2)
            window.figure.tight_layout()
            window.canvas.draw_idle()

        window.table.bind("<<TreeviewSelect>>", plot)
        tk.Button(window.options, text="Проверить", command=refresh).pack(side="left")
        refresh()
        return window

    def open_integral_stability(self):
        # Keep the empty-window startup independent of optional diagnostics.
        from integral_stability_view import open_integral_stability

        return open_integral_stability(self)

    def open_sensitivity(self):
        if not self.workflow_available():
            return
        records = copy.deepcopy(self.calculation_records())
        blocks = copy.deepcopy(self.formula_document.blocks)
        if not any(b["kind"] == "output" for b in blocks.values()):
            messagebox.showinfo(
                "Устойчивость",
                "Выберите готовую формулу или добавьте вывод в редакторе формул.\n"
                "Для проверки самих интегралов без формулы: Обработка сигналов → Устойчивость интегралов…",
                parent=self,
            )
            return
        contexts = {
            r["id"]: {
                bid: copy.deepcopy(self.integral_context(r, b))
                for bid, b in blocks.items()
                if b["kind"] == "area"
            }
            for r in records
        }
        absolute = self.integration_method.get() == "Сумма модулей относительно 0"
        window = ResultsWindow(
            self,
            "Himera · Устойчивость расчёта",
            ("Спектр", "Сценарий", "Вывод", "Значение", "Состояние"),
            modal=True,
        )
        delta, percent = tk.StringVar(value="0.001"), tk.StringVar(value="5")
        for label, var in (("δ границ, ppm", delta), ("Изменение вычитания линии, %", percent)):
            tk.Label(window.options, text=label).pack(side="left", padx=4)
            ttk.Entry(window.options, textvariable=var, width=8).pack(side="left", padx=4)

        def run():
            try:
                d, p = float(delta.get().replace(",", ".")), float(percent.get().replace(",", "."))
                if not np.isfinite(d) or d <= 0 or not 0 < p <= 50:
                    raise ValueError("δ > 0; изменение линии от 0 до 50%")
            except ValueError as error:
                window.status.set(str(error))
                return
            run_button.configure(state="disabled")

            def work(stop):
                rows = []
                total = sum(sensitivity_case_count(r, contexts[r["id"]]) for r in records)
                completed = 0
                for index, record in enumerate(records, 1):
                    if stop.is_set():
                        break

                    def progress(count, _planned, label):
                        window.report_progress(
                            completed + count,
                            total,
                            f"Спектр {index}/{len(records)} · {record['name']} · {label}",
                        )

                    try:
                        result = analyze_sensitivity(
                            record,
                            blocks,
                            contexts[record["id"]],
                            delta=d,
                            baseline_percent=p,
                            absolute=absolute,
                            stop=stop,
                            progress=progress,
                            keep_partial=True,
                        )
                    except Exception as error:
                        # One damaged spectrum must not erase completed checks
                        # from earlier spectra, or look like a valid zero output.
                        result = [
                            dict(
                                case="Исходный",
                                output_id=bid,
                                name=block.get("name") or "Вывод",
                                value=None,
                                status="error",
                                message=str(error),
                                planned_cases=sensitivity_case_count(
                                    record, contexts[record["id"]]
                                ),
                                completed_case=False,
                            )
                            for bid, block in blocks.items()
                            if block["kind"] == "output"
                        ]
                    rows.extend(
                        dict(r, spectrum=record["name"], spectrum_id=record["id"]) for r in result
                    )
                    completed += len({r["case"] for r in result if r.get("completed_case", True)})
                return rows

            def done(rows):
                run_button.configure(state="normal")
                window.set_rows(
                    [(r["spectrum"], r["case"], r["name"], r["value"], r["message"]) for r in rows]
                )
                summaries = []
                for record in records:
                    for summary in summarize_sensitivity(
                        [r for r in rows if r["spectrum_id"] == record["id"]]
                    ):
                        summaries.append(
                            f"{record['name']} · {summary['name']}: {format_value(summary['low'])} … {format_value(summary['high'])}; макс. изменение {format_value(summary['percent'])}%"
                            + (
                                f"; неготовых сценариев: {summary['failed']}"
                                if summary["failed"]
                                else ""
                            )
                            + (
                                f"; НЕПОЛНАЯ проверка: {summary['completed']}/{summary['planned']} сценариев"
                                if summary["partial"]
                                else ""
                            )
                        )
                completed = len(
                    {(r["spectrum_id"], r["case"]) for r in rows if r.get("completed_case", True)}
                )
                planned = sum(sensitivity_case_count(r, contexts[r["id"]]) for r in records)
                window.status.set(
                    f"Завершено сценариев: {completed}/{planned}. "
                    + ("Проверка неполная.\n" if completed < planned else "\n")
                    + "\n".join(summaries[:6])
                    + "\nДиапазон относится только к этим изменениям, это не погрешность измерения. Группы Spintegrate сопоставляются заново; потерянные линии дают ошибку."
                )
                self.last_sensitivity = rows if completed == planned else None
                self._sensitivity_snapshot = (
                    copy.deepcopy(records),
                    copy.deepcopy(self.formula_document.snapshot()),
                    copy.deepcopy(self.comparisons),
                    self.integration_method.get(),
                )

            def failed(_message):
                run_button.configure(state="normal")
                self.last_sensitivity = None
                self._sensitivity_snapshot = None

            window.start(work, done, failed=failed)

        run_button = tk.Button(window.options, text="Оценить", command=run)
        run_button.pack(side="left", padx=8)
        window.status.set(
            "Проверка: границы шире/уже, фон ±σ, изменение величины вычитания линии и локального λ Spintegrate. Исходные данные сохраняются."
        )
        return window

    def capture_workflow_state(self, ids):
        return dict(
            **self.capture_records(ids),
            formula=self.capture_formula_state(),
            comparison_id=self.current_comparison_id,
            next_comparison_id=self._next_comparison_id,
        )

    def open_processing_templates(self):
        if not self.workflow_available():
            return
        window = tk.Toplevel(self)
        window.title("Himera · Шаблоны обработки серии")
        window.geometry("730x570")
        window.minsize(650, 500)
        window.transient(self)
        body = tk.Frame(window, padx=12, pady=12)
        body.pack(fill="both", expand=True)
        tk.Label(
            body,
            text="Шаблон: фаза, коррекция, выравнивание, интегралы и формула.",
            font="AppHeadingFont",
        ).pack(anchor="w")
        tk.Label(
            body,
            text="Создайте шаблон по обработанному спектру. Перед применением будет предпросмотр.\nТекущие интегралы выбранных спектров заменяются; Ctrl+Z отменяет всю серию.",
            justify="left",
        ).pack(anchor="w", pady=8)
        directory = Path(__file__).parent / "processing_presets"
        choice = tk.StringVar()
        picker = ttk.Combobox(body, textvariable=choice, state="readonly")
        picker.pack(fill="x", pady=4)
        paths = {}

        def refresh(selected=None):
            paths.clear()
            for path in sorted(directory.glob("*.json")):
                paths[path.stem] = path
            if selected:
                paths[Path(selected).stem] = Path(selected)
            picker.configure(values=list(paths))
            if selected:
                choice.set(Path(selected).stem)
            elif paths:
                picker.current(0)

        def save():
            self.save_active_spectrum()
            record = self.spectra[self.active_spectrum_id]
            items, offset = self.integral_context(record)
            if not items:
                messagebox.showinfo("Шаблон", "Сначала создайте интегралы.", parent=window)
                return
            try:
                recipe = make_template(
                    record,
                    items,
                    self.formula_preset_data(),
                    offset=offset,
                    limits=sorted(self.axes.get_xlim()),
                )
            except ValueError as error:
                messagebox.showerror("Шаблон", str(error), parent=window)
                return
            directory.mkdir(exist_ok=True)
            filename = filedialog.asksaveasfilename(
                parent=window,
                title="Сохранить шаблон обработки",
                initialdir=directory,
                initialfile=f"{record['name']}.json",
                defaultextension=".json",
                filetypes=(("Шаблон Himera", "*.json"),),
            )
            if filename:
                try:
                    Path(filename).write_text(
                        json.dumps(recipe, ensure_ascii=False, indent=2, allow_nan=False),
                        encoding="utf-8",
                    )
                    refresh(filename)
                except OSError as error:
                    messagebox.showerror("Шаблон", str(error), parent=window)

        def load():
            filename = filedialog.askopenfilename(
                parent=window,
                title="Выбрать шаблон обработки",
                initialdir=directory,
                filetypes=(("Шаблон Himera", "*.json"),),
            )
            if filename:
                try:
                    load_template(filename)
                    refresh(filename)
                except (ValueError, OSError) as error:
                    messagebox.showerror("Шаблон", str(error), parent=window)

        buttons = tk.Frame(body)
        buttons.pack(fill="x", pady=5)
        tk.Button(buttons, text="Сохранить по текущему спектру…", command=save).pack(side="left")
        tk.Button(buttons, text="Открыть шаблон…", command=load).pack(side="left", padx=5)
        tk.Label(body, text="Спектры для обработки (Ctrl / Shift):").pack(anchor="w", pady=(8, 4))
        spectra = tk.Listbox(body, selectmode="extended", exportselection=False, height=8)
        spectra.pack(fill="both", expand=True)
        ids = list(self.spectra)
        for i, sid in enumerate(ids):
            spectra.insert("end", self.spectra[sid]["name"])
            if sid == self.active_spectrum_id:
                spectra.selection_set(i)
        align, auto_phase, formulas = (
            tk.BooleanVar(value=True),
            tk.BooleanVar(value=False),
            tk.BooleanVar(value=True),
        )
        for label, var in (
            ("Выравнивать по образцу (до ±0.05 ppm)", align),
            ("Автофаза для каждого спектра вместо фазы образца", auto_phase),
            ("Применить формулу шаблона", formulas),
        ):
            tk.Checkbutton(body, text=label, variable=var).pack(anchor="w", pady=2)

        def preview():
            if not self.workflow_available():
                return
            selected = [ids[i] for i in spectra.curselection()]
            if not selected or choice.get() not in paths:
                messagebox.showinfo("Шаблон", "Выберите шаблон и спектры.", parent=window)
                return
            try:
                recipe = load_template(paths[choice.get()])
            except (ValueError, OSError) as error:
                messagebox.showerror("Шаблон", str(error), parent=window)
                return
            options = dict(align=align.get(), auto_phase=auto_phase.get())
            use_formula = formulas.get()
            window.destroy()
            old = self.capture_workflow_state(selected)
            self.preview_processing_template(recipe, old, options, use_formula)

        tk.Button(body, text="Предпросмотр обработки…", command=preview).pack(anchor="e", pady=10)
        refresh()
        return window

    def preview_processing_template(self, recipe, old, options, use_formula):
        window = ResultsWindow(
            self,
            "Himera · Предпросмотр обработки серии",
            ("Спектр", "Сдвиг, ppm", "Корреляция", "φ₀, °", "φ₁, °", "Интегралов", "Результат"),
            plot=True,
            modal=True,
        )
        window.outputs = {}
        apply_button = tk.Button(window.footer, text="Применить серию", state="disabled")
        apply_button.pack(side="right", padx=6)

        def work(stop):
            outputs, rows = {}, []
            for index, (sid, record) in enumerate(old["records"].items()):
                if stop.is_set():
                    return outputs, rows
                window.report_progress(index, len(old["records"]), f"Спектр {record['name']}")
                try:
                    result, diagnostic = apply_template(record, recipe, **options)
                    outputs[sid] = result
                    engine = FormulaEngine(
                        FormulaDocument.from_preset(recipe["formula"], result["integrals"]).blocks,
                        lambda block, r=result: self.template_area(r, block),
                    )
                    answers = engine.outputs()
                    summary = (
                        "; ".join(
                            f"{name}: {format_value(value.value)} ({value.message})"
                            for _, name, value in answers
                        )
                        or "Обработка готова"
                    )
                    rows.append(
                        (
                            record["name"],
                            diagnostic["shift"],
                            diagnostic["correlation"],
                            result["phase0"],
                            result["phase1"],
                            len(result["integrals"]),
                            summary,
                        )
                    )
                except Exception as error:
                    rows.append(
                        (record["name"], None, None, None, None, 0, "Ошибка: " + str(error))
                    )
                window.report_progress(index + 1, len(old["records"]), f"Спектр {record['name']}")
            return outputs, rows

        def done(payload):
            outputs, rows = payload
            window.outputs = outputs
            window.set_rows(rows)
            window.status.set(
                f"Готово {len(outputs)} из {len(old['records'])}. После применения выберите спектр слева и проверьте фазу, фон и интегралы. Ctrl+Z отменяет серию."
            )
            apply_button.configure(
                state="normal" if len(outputs) == len(old["records"]) else "disabled"
            )
            if rows:
                window.table.selection_set("0")
                plot()

        def plot(_event=None):
            selection = window.table.selection()
            if not selection:
                return
            sid = list(old["records"])[int(selection[0])]
            ax = window.axes
            ax.clear()
            if sid in window.outputs:
                record = window.outputs[sid]
                ax.plot(record["ppm"], current_signal(record), lw=0.8)
                for item in record["integrals"]:
                    ax.axvspan(item["left"], item["right"], color=item["color"], alpha=0.1)
                ax.set_xlim(float(np.max(record["ppm"])), float(np.min(record["ppm"])))
            ax.set_xlabel("Химический сдвиг, ppm")
            ax.grid(alpha=0.2)
            window.figure.tight_layout()
            window.canvas.draw_idle()

        def apply():
            if len(window.outputs) != len(old["records"]):
                return
            self.save_active_spectrum()
            if not all(
                same_state(self.spectra.get(sid), r) for sid, r in old["records"].items()
            ) or not same_state(self.comparisons, old["comparisons"]):
                window.status.set(
                    "Данные изменились во время предпросмотра. Откройте шаблон заново."
                )
                return
            for sid, record in window.outputs.items():
                self.spectra[sid] = record
            self.current_comparison_id = None
            self.restore_active_spectrum(self.active_spectrum_id)
            if len(window.outputs) > 1:
                cid = self._next_comparison_id
                self._next_comparison_id += 1
                self.comparisons[cid] = dict(
                    id=cid,
                    name=f"Серия · {recipe['name']}",
                    spectrum_ids=list(window.outputs),
                    mode="separate",
                    offsets={sid: 0.0 for sid in window.outputs},
                    integrals=copy.deepcopy(recipe["integrals"]),
                    next_integral_id=max((i["id"] for i in recipe["integrals"]), default=0) + 1,
                )
                self.current_comparison_id = cid
            if use_formula:
                self.formula_document = FormulaDocument.from_preset(
                    recipe["formula"], self.integrals, self.current_comparison_id
                )
                self.active_preset_key = None
                if self.formula_editor is not None:
                    self.formula_editor.document = self.formula_document
                    self.formula_editor.reset_interaction()
            self._undo_stack.append(("workflow_records", "обработка серии по шаблону", old))
            self._redo_stack.clear()
            self.trim_history()
            window.destroy()
            self.refresh_spectrum_library()
            self.show_integral_table()
            self.draw()
            self.refresh_formula_workspace()
            self.status.set(f"Обработано спектров: {len(old['records'])}. Ctrl+Z — отменить серию.")

        apply_button.configure(command=apply)
        window.table.bind("<<TreeviewSelect>>", plot)
        window.start(work, done)
        return window

    @staticmethod
    def template_area(record, block):
        from sensitivity import area_for_context

        item = next(
            (i for i in record["integrals"] if i["number"] == block.get("area_number")), None
        )
        return (
            area_for_context(
                record, item, 0, record["integration_method"] == "Сумма модулей относительно 0"
            )
            if item
            else None
        )

    def export_analysis_report(self):
        if not self.workflow_available():
            return
        records = self.calculation_records()
        if not records:
            return
        filename = filedialog.asksaveasfilename(
            parent=self,
            title="Экспорт отчёта Himera",
            defaultextension=".html",
            initialfile="Himera_report.html",
            filetypes=(("Отчёт HTML (печать в PDF из браузера)", "*.html"),),
        )
        if not filename:
            return
        entries = []
        quality = self.quality_entries(records, calculation=True)
        for record in records:
            items, offset = self.integral_context(record, calculation=True)
            values = []
            for item in items:
                diagnostic = next(
                    (
                        q["quality"]
                        for q in quality
                        if q["record"]["id"] == record["id"] and q["item"]["id"] == item["id"]
                    ),
                    {},
                )
                values.append(
                    [
                        item["number"],
                        item["name"],
                        f"{item['left']:.6f} … {item['right']:.6f}",
                        self.record_integral_area(
                            record, item["left"], item["right"], x_offset=offset, integral=item
                        ),
                        self.record_integral_display_value(
                            record, item["left"], item["right"], x_offset=offset, integral=item
                        ),
                        "Spintegrate" if item.get("spin_fit_id") else "Численный",
                        "; ".join(diagnostic.get("warnings", []))
                        or "Нет заметных признаков проблемы",
                    ]
                )
            entries.append(
                dict(
                    record=copy.deepcopy(record),
                    items=copy.deepcopy(items),
                    offset=offset,
                    integrals=values,
                    method=self.integration_method.get(),
                    display=self.integration_display.get(),
                    outputs=[
                        [name, result.value, result.message]
                        for _, name, result in self.formula_engine_for_record(record).outputs()
                    ],
                    formula_inputs=self.report_formula_inputs(record),
                )
            )
        snapshot = (
            records,
            self.formula_document.snapshot(),
            self.comparisons,
            self.integration_method.get(),
        )
        sensitivity = (
            copy.deepcopy(self.last_sensitivity)
            if same_state(snapshot, self._sensitivity_snapshot)
            else None
        )
        formula = self.formula_preset_data()
        window = ResultsWindow(self, "Himera · Экспорт отчёта", ("Файл", "Состояние"))

        def done(written):
            if not written:
                window.set_rows([(filename, "Остановлено; файл не записан")])
                window.status.set("Остановлено. Экспорт не записан; существующий файл не изменён.")
                return
            window.set_rows([(filename, "Сохранён")])
            window.status.set(
                "Самодостаточный HTML: графики, параметры обработки, интегралы, формулы и результаты. Откройте в браузере; для PDF используйте печать."
            )

        window.start(lambda stop: write_report(filename, entries, formula, sensitivity, stop), done)
        return window

    def report_formula_inputs(self, record):
        values = []
        for bid, block in self.formula_document.blocks.items():
            if block["kind"] != "area":
                continue
            item, offset = self.integral_context(record, block)
            values.append(
                [
                    bid,
                    f"I{block.get('area_number', '?')}",
                    f"{item['left']:.6f} … {item['right']:.6f}" if item else "Отсутствует",
                    offset,
                    self.resolve_formula_area(block, record),
                ]
            )
        return values
