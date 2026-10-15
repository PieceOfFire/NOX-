"""Shared live Spintegrate parameters and the legacy programmatic setup dialog."""

from __future__ import annotations
import copy
import tkinter as tk
from tkinter import messagebox, ttk
import math
from spin_backend import default_recipe, engine
from spin_tuning import PARAMETERS, adjusted_value, suggest_parameters
from ui_helpers import add_tooltip
from dataclasses import asdict
from spintegrate.profiles import SHAPE_LABELS
from spin_controls import ParameterPage
from baseline_policy import corrected_region, resolve_spin_baseline

PEAK_LABELS = {"prominence": "По выраженности (шумоустойчивый)", "height": "По высоте (старый)"}


class SpinParameters(ttk.Frame):
    """Shared parameter controls for initial selection and live fitting."""

    def __init__(self, parent, recipe=None, on_change=None, on_auto=None):
        core = engine()
        self.recipe = copy.deepcopy(recipe) if recipe else default_recipe()
        self.recipe.setdefault(
            "baseline_fallback_method", self.recipe["baseline_default"]["method"]
        )
        self.recipe["fit"] = {**asdict(core.FitSettings()), **self.recipe["fit"]}
        self.recipe["fit"]["line_shape"] = self.recipe.get(
            "group_default_shape", self.recipe["fit"]["line_shape"]
        )
        super().__init__(parent)
        self.vars = {}
        self.entries = {}
        self.labels = {}
        self.on_change, self.on_auto = on_change, on_auto
        self._updating = False
        self._baseline_context = None
        self.wheel_remainder = {}
        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(0, 4))
        self.auto_button = tk.Button(
            actions,
            text="Автонастройка",
            command=self.auto_start,
            state="normal" if on_auto else "disabled",
        )
        self.auto_button.pack(side="left")
        add_tooltip(
            self.auto_button,
            "Оценить стартовый порог, ширины и параметры линии по области. Затем проверьте подгонку.",
        )
        ttk.Label(
            actions, text="Колесо / ↑↓: шаг\nShift: точно · Ctrl: крупно", foreground="#666666"
        ).pack(side="left", padx=10)
        self.feedback = tk.StringVar()
        ttk.Label(
            body, textvariable=self.feedback, wraplength=430, foreground="#666666", justify="left"
        ).pack(side="bottom", fill="x", pady=(4, 0))
        tabs = ttk.Notebook(body)
        self.tabs = tabs
        tabs.pack(fill="both", expand=True)
        self.pages = [ParameterPage(tabs) for _ in range(4)]
        for page, title in zip(
            self.pages, ("Поиск линий", "Модель", "Базовая линия", "Дополнительно")
        ):
            tabs.add(page, text=title)
        fit_tab, model_tab, baseline_tab, advanced_tab = [p.body for p in self.pages]
        self.field(
            model_tab,
            0,
            "Форма линии",
            "fit.line_shape",
            SHAPE_LABELS[self.recipe["fit"]["line_shape"]],
            tuple(SHAPE_LABELS.values()),
        )
        self.refine_centers = tk.BooleanVar(value=self.recipe["fit"]["refine_centers"])
        ttk.Checkbutton(model_tab, text="Уточнять центры линий", variable=self.refine_centers).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=8
        )
        self.field(
            model_tab,
            2,
            "Смещение центра, не более Гц",
            "fit.max_center_shift_hz",
            self.recipe["fit"]["max_center_shift_hz"],
        )
        ttk.Label(
            model_tab,
            wraplength=350,
            foreground="#666666",
            text="Voigt — свёртка Лоренца и Гаусса. Асимметричный профиль уточняет левую и правую ширины.\n\n"
            "Voigt с дисперсией учитывает небольшую общую фазовую примесь модели; спектр NOX не меняется.\n\n"
            "Для отдельного интеграла: выделите его в таблице → «Форма…». Высокий R² не гарантирует точность площадей.",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=12)
        for row, (label, key, choices) in enumerate(
            (
                ("Порог поиска линий", "threshold", None),
                ("Интервал между линиями, точки", "min_separation_points", None),
                ("Начальная FWHM, Гц", "initial_width_hz", None),
                ("Минимальная FWHM, Гц", "min_width_hz", None),
                ("Максимальная FWHM, Гц", "max_width_hz", None),
                ("Попытки подгонки", "restarts", None),
                ("Лимит вычислений", "max_nfev", None),
                ("Лимит линий модели", "max_peaks", None),
                ("Функция ошибки", "loss", ("linear", "soft_l1")),
                ("Предел асимметрии (отношение)", "max_asymmetry_ratio", None),
                ("Предел фазы модели, °", "max_profile_phase_degrees", None),
                ("Поиск линий", "peak_detection", tuple(PEAK_LABELS.values())),
                ("Мин. выраженность (0 — авто)", "min_prominence", None),
            )
        ):
            self.field(
                fit_tab if row < 3 else advanced_tab,
                row if row < 3 else row - 3,
                label,
                "fit." + key,
                PEAK_LABELS[self.recipe["fit"][key]]
                if key == "peak_detection"
                else self.recipe["fit"][key],
                choices,
            )
        self.signed = tk.BooleanVar(value=self.recipe["fit"]["signed"])
        self.auto_threshold = tk.BooleanVar(value=self.recipe.get("auto_threshold", False))
        ttk.Checkbutton(
            fit_tab, text="Искать также отрицательные линии", variable=self.signed
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(
            fit_tab, text="Определять порог по шуму выбранной области", variable=self.auto_threshold
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(
            fit_tab,
            text="Поиск по выраженности отличает пики от неровностей хвостов.\nПорог выше → меньше слабых линий.\nЭто настройки модели; итоговые интегралы создаются справа.",
            wraplength=390,
            justify="left",
            foreground="#666666",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Label(
            advanced_tab,
            text="Лимит линий защищает от слишком сложной подгонки.\nОн не ограничивает число итоговых интегралов.",
            wraplength=390,
            justify="left",
            foreground="#666666",
        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=8)
        for row, (label, key, choices) in enumerate(
            (
                ("Метод", "method", core.BASELINE_METHODS),
                ("Сглаживание λ", "lam", None),
                ("Асимметрия p", "p", None),
                ("Окно SNIP / mor, точки", "half_window", None),
                ("Окно сглаживания, точки", "smooth_half_window", None),
                ("Порядок полинома", "poly_order", None),
                ("Лимит итераций", "max_iter", None),
                ("Точность tol", "tol", None),
            )
        ):
            self.field(
                baseline_tab,
                row,
                label,
                "baseline." + key,
                self.recipe["baseline_default"][key],
                choices,
            )
        ttk.Label(
            baseline_tab,
            text="Линия вычитается только внутри выбранной области для подгонки.\n"
            "Сам исходный спектр и обычные интегралы не меняются.",
            wraplength=330,
            foreground="#68707d",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=12)
        self.follow_nox = tk.BooleanVar(value=self.recipe.get("baseline_policy", "manual") == "nox")
        ttk.Checkbutton(
            baseline_tab, text="Учитывать коррекцию NOX автоматически", variable=self.follow_nox
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=4)
        self.baseline_context_note = tk.StringVar()
        ttk.Label(
            baseline_tab,
            textvariable=self.baseline_context_note,
            wraplength=330,
            foreground="#68707d",
        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=4)
        for path, variable in self.vars.items():
            variable.trace_add("write", lambda *_, key=path: self.parameter_changed(key))
        for variable in (self.signed, self.auto_threshold, self.refine_centers):
            variable.trace_add("write", lambda *_: self.parameter_changed())
        self.follow_nox.trace_add("write", lambda *_: self.parameter_changed("baseline_policy"))
        for page in self.pages:
            page.bind_scrolling()

    def field(self, parent, row, label, key, value, choices=None):
        variable = tk.StringVar(value=f"{value:.8g}" if isinstance(value, float) else str(value))
        self.vars[key] = variable
        self.labels[key] = label
        ttk.Label(parent, text=label, wraplength=160).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=3
        )
        if choices:
            widget = ttk.Combobox(
                parent, textvariable=variable, values=choices, state="readonly", width=16
            )
        else:
            widget = ttk.Frame(parent)
            entry = ttk.Entry(widget, textvariable=variable, width=10)
            entry.pack(side="left", fill="x", expand=True)
            self.entries[key] = entry
            for symbol, direction in (("−", -1), ("+", 1)):
                tk.Button(
                    widget,
                    text=symbol,
                    width=2,
                    padx=2,
                    pady=0,
                    command=lambda k=key, d=direction: self.nudge(k, d),
                ).pack(side="left", padx=(2, 0))
            entry.bind("<MouseWheel>", lambda event, k=key: self.on_wheel(event, k))
            entry.bind("<Button-4>", lambda event, k=key: self.on_wheel(event, k))
            entry.bind("<Button-5>", lambda event, k=key: self.on_wheel(event, k))
            entry.bind("<Up>", lambda event, k=key: self.nudge(k, 1, event))
            entry.bind("<Down>", lambda event, k=key: self.nudge(k, -1, event))
            add_tooltip(
                entry,
                "Колесо: изменить значение. Shift — в 10 раз точнее; Ctrl — крупный шаг. Порог и λ меняются относительно текущего значения.",
            )
        widget.grid(row=row, column=1, sticky="ew", pady=3)
        parent.columnconfigure(1, weight=1)

    def parameter_changed(self, path=None):
        if self._updating:
            return
        if path == "baseline.method":
            self._updating = True
            self.follow_nox.set(False)
            self._updating = False
            self.recipe["baseline_fallback_method"] = self.vars[path].get()
        if path in ("baseline.method", "baseline_policy"):
            self.update_baseline_context()
        if path == "fit.threshold" and self.auto_threshold.get():
            self._updating = True
            self.auto_threshold.set(False)
            self._updating = False
            self.feedback.set("Порог изменён вручную: автоматический порог отключён.")
        if self.on_change:
            self.on_change()

    def set_baseline_context(self, record, low, high, offset=0):
        self._baseline_context = (record, low, high, offset)
        self.update_baseline_context()

    def update_baseline_context(self):
        if self._baseline_context is None:
            return
        record, low, high, offset = self._baseline_context
        corrected = corrected_region(record, low, high, offset)
        self.baseline_context_note.set(
            "Фон уже вычтен в NOX на этой области → none, без повторного вычитания."
            if corrected and self.follow_nox.get()
            else "Фон уже вычтен в NOX. Локальный метод выбран вручную."
            if corrected
            else "Область не полностью скорректирована в NOX. Используется выбранный локальный метод."
        )
        if self.follow_nox.get():
            recipe = {**self.recipe, "baseline_policy": "nox"}
            resolved = resolve_spin_baseline(recipe, record, low, high, offset)
            self._updating = True
            try:
                self.vars["baseline.method"].set(resolved["baseline_default"]["method"])
            finally:
                self._updating = False

    def nudge(self, path, direction, event=None):
        state = getattr(event, "state", 0)
        try:
            value = adjusted_value(
                path, self.vars[path].get(), direction, fine=bool(state & 1), coarse=bool(state & 4)
            )
            # Keep width bounds valid while turning the wheel; typing stays available.
            if path in ("fit.min_width_hz", "fit.max_width_hz"):
                other = "fit.max_width_hz" if path == "fit.min_width_hz" else "fit.min_width_hz"
                bound = float(self.vars[other].get().replace(",", "."))
                if not math.isfinite(bound) or bound <= 0:
                    raise ValueError("Сначала исправьте соседнюю границу ширины.")
                if path == "fit.min_width_hz":
                    value = min(value, bound * 0.999)
                else:
                    value = max(value, bound * 1.001)
            self.vars[path].set(str(value) if PARAMETERS[path][-1] else f"{value:.10g}")
        except (ValueError, OverflowError):
            self.feedback.set("Для изменения колесом в поле должно быть корректное число.")
        return "break"

    def on_wheel(self, event, path):
        state = getattr(event, "state", 0) & 5
        key = (path, state)
        if getattr(event, "num", None) in (4, 5):
            steps = 1 if event.num == 4 else -1
        else:
            accumulated = self.wheel_remainder.get(key, 0) + event.delta
            steps = math.trunc(accumulated / 120)
            self.wheel_remainder[key] = accumulated - steps * 120
        if steps:
            self.nudge(path, steps, event)
        return "break"

    def set_recipe(self, recipe, summary="", *, notify=True):
        self._updating = True
        try:
            self.recipe = copy.deepcopy(recipe)
            self.recipe.setdefault(
                "baseline_fallback_method", self.recipe["baseline_default"]["method"]
            )
            self.recipe["fit"] = {**asdict(engine().FitSettings()), **self.recipe["fit"]}
            self.recipe["fit"]["line_shape"] = self.recipe.get(
                "group_default_shape", self.recipe["fit"]["line_shape"]
            )
            for path, variable in self.vars.items():
                group, key = path.split(".")
                value = self.recipe["baseline_default" if group == "baseline" else "fit"][key]
                if key == "line_shape":
                    value = SHAPE_LABELS[value]
                elif key == "peak_detection":
                    value = PEAK_LABELS[value]
                variable.set(f"{value:.8g}" if isinstance(value, float) else str(value))
            self.signed.set(recipe["fit"]["signed"])
            self.refine_centers.set(self.recipe["fit"]["refine_centers"])
            self.auto_threshold.set(recipe.get("auto_threshold", False))
            self.follow_nox.set(recipe.get("baseline_policy", "manual") == "nox")
            self.feedback.set(summary)
        finally:
            self._updating = False
        self.update_baseline_context()
        if notify and self.on_change:
            self.on_change()

    def auto_start(self):
        if self.on_auto:
            self.on_auto()

    def collect_recipe(self):
        recipe = copy.deepcopy(self.recipe)
        recipe.update(source="current", regions=[], auto_threshold=self.auto_threshold.get())
        recipe["baseline_policy"] = "nox" if self.follow_nox.get() else "manual"
        recipe["fit"]["signed"] = self.signed.get()
        recipe["fit"]["refine_centers"] = self.refine_centers.get()
        integers = {
            "min_separation_points",
            "restarts",
            "max_nfev",
            "max_peaks",
            "half_window",
            "smooth_half_window",
            "poly_order",
            "max_iter",
        }
        for path, variable in self.vars.items():
            group, key = path.split(".")
            text = variable.get().strip().replace(",", ".")
            if key == "line_shape":
                value = next(k for k, label in SHAPE_LABELS.items() if label == text)
            elif key == "peak_detection":
                value = next(k for k, label in PEAK_LABELS.items() if label == text)
            else:
                if key in ("method", "loss"):
                    value = text
                else:
                    try:
                        value = float(text)
                        if not math.isfinite(value):
                            raise ValueError()
                    except ValueError:
                        raise ValueError(
                            f"«{self.labels[path]}»: введите конечное число."
                        ) from None
            if key in integers:
                if value != int(value):
                    raise ValueError(f"«{self.labels[path]}»: требуется целое число.")
                value = int(value)
            recipe["baseline_default" if group == "baseline" else "fit"][key] = value
        recipe["group_default_shape"] = recipe["fit"]["line_shape"]
        if not self.follow_nox.get():
            recipe["baseline_fallback_method"] = recipe["baseline_default"]["method"]
        core = engine()
        core.Session(
            fit=core.FitSettings(**recipe["fit"]),
            baseline_default=core.BaselineSettings(**recipe["baseline_default"]),
        ).validate()
        return recipe


class SpinDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        app._spectrum_dialog_open = True
        self.title("Spintegrate · Параметры деконволюции")
        self.transient(app)
        self.grab_set()
        self.geometry("590x525")
        self.minsize(560, 500)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="Задайте параметры и выделите область на графике.\n"
            "Затем откроется живой предпросмотр с разделением на интегралы.",
        ).pack(anchor="w")
        footer = ttk.Frame(body)
        footer.pack(side="bottom", fill="x")
        tk.Button(footer, text="Отмена", command=self.destroy).pack(side="right")
        tk.Button(footer, text="Выбрать область на графике", command=self.run).pack(
            side="right", padx=8
        )
        self.parameters = SpinParameters(body, app.spin_settings, on_auto=self.auto_start)
        app.save_active_spectrum()
        record = app.spectra.get(app.active_spectrum_id)
        if record is not None:
            comparison = app.comparison_record()
            offsets = comparison.get("offsets", {}) if comparison else {}
            offset = float(
                offsets.get(app.active_spectrum_id, offsets.get(str(app.active_spectrum_id), 0))
            )
            self.parameters.set_baseline_context(record, *sorted(app.axes.get_xlim()), offset)
        self.parameters.pack(fill="both", expand=True)
        self.vars = self.parameters.vars

    def auto_start(self):
        self.app.save_active_spectrum()
        record = self.app.spectra[self.app.active_spectrum_id]
        comparison = self.app.comparison_record()
        offsets = comparison.get("offsets", {}) if comparison else {}
        sid = self.app.active_spectrum_id
        offset = float(offsets.get(sid, offsets.get(str(sid), 0)))
        try:
            recipe, note = suggest_parameters(
                record, self.collect_recipe(), *sorted(self.app.axes.get_xlim()), offset=offset
            )
            self.parameters.set_recipe(recipe, "По видимому участку. " + note)
        except (ValueError, RuntimeError, OverflowError) as error:
            self.parameters.feedback.set(str(error))

    def collect_recipe(self):
        return self.parameters.collect_recipe()

    def destroy(self):
        self.app._spectrum_dialog_open = False
        super().destroy()

    def run(self):
        try:
            recipe = self.collect_recipe()
        except (ValueError, OverflowError) as error:
            messagebox.showerror("Spintegrate", str(error), parent=self)
            return
        self.destroy()
        self.app.arm_spin_selection(recipe)
