"""Оконный просмотрщик и корректор 1D NMR Bruker."""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector

from alignment import AlignmentError, find_x_shift
from base_line import (
    fit_lorentzian_peak,
    load_bruker_spectrum,
    lorentzian_component,
    peak_polarity,
    phase_correct,
)
from nmr_io import choose_windows_folders as _choose_windows_folders
from project_store import (
    decode_signal,
    encode_signal,
    read_project,
    write_project as write_project_file,
)
from formula_document import FormulaDocument
from formula_engine import FormulaEngine
from formula_presets import PresetLibrary
from calculations_view import CalculationsView
from integration import integrate_region
from integral_editor import IntegralBoundaryEditor
from ui_helpers import add_tooltip
from ui_theme import install_theme
from view_preferences import (
    capture_table_view,
    read_preferences,
    restore_table_view,
    usable_limits,
    visible_geometry,
    write_preferences,
)
from spectrum_binding import ActiveSpectrum, SpectrumVariable
from spectrum_state import current_signal, same_state
from spectrum_library import (
    SpectrumLibrary,
    clean_title,
    library_status,
    matches_search,
    read_bruker_title,
    spectrum_details,
    spectrum_label,
)

try:
    # Tk сам по себе не умеет принимать файлы из Проводника. tkinterdnd2
    # добавляет нативный Windows drag-and-drop и остаётся необязательным для
    # запуска уже сохранённых проектов.
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _RootWindow = TkinterDnD.Tk
    DND_ENABLED = True
except ImportError:
    DND_FILES = "DND_Files"
    _RootWindow = tk.Tk
    DND_ENABLED = False


def choose_windows_folders(parent_hwnd: int) -> tuple[str, ...] | None:
    """Открыть нативный Проводник Windows с Ctrl/Shift для папок.

    ``None`` означает, что системный COM-диалог недоступен; пустой кортеж —
    что пользователь просто нажал «Отмена».
    """
    return _choose_windows_folders(parent_hwnd)


class SpectrumApp(ActiveSpectrum, _RootWindow):
    add_formula_tooltip = staticmethod(add_tooltip)
    DECONVOLUTION_METHOD = "Деконволюция"
    LEGACY_LORENTZIAN_METHOD = "Аппроксимация Лоренца"
    INTEGRATION_METHODS = (
        "Обычная площадь",
        "Сумма модулей относительно 0",
    )
    INTEGRATION_DISPLAYS = (
        "Площадь",
        "Сумма дискретных точек (MestReNova)",
    )

    @classmethod
    def normalize_integration_method(cls, method: str) -> str:
        """Безопасно открыть проект, сохранённый с экспериментальным методом."""
        if method in (cls.LEGACY_LORENTZIAN_METHOD, cls.DECONVOLUTION_METHOD):
            return "Обычная площадь"
        return method if method in cls.INTEGRATION_METHODS else "Обычная площадь"

    def __init__(self) -> None:
        super().__init__()
        self.initialize_spectrum_store()
        install_theme(self)
        self.title("NOX · Обработка спектров")
        self.minsize(950, 620)
        self._view_preferences = read_preferences()
        self.linked_ppm_view = tk.BooleanVar(value=self._view_preferences["linked_ppm"])
        self.auto_y_on_switch = tk.BooleanVar(value=self._view_preferences["auto_y"])
        self.library_visible = tk.BooleanVar(value=self._view_preferences["library_visible"])
        self.integral_table_visible = tk.BooleanVar(value=self._view_preferences["table_visible"])
        self._plot_views = {}
        self._integral_table_views = {}
        self._last_integral_table_context = None
        self._integral_table_refreshing = False
        self.project_path: Path | None = None
        self.source_folder: Path | None = None
        self.ppm_shift = 0.0
        # История едина для фазировки, базовой линии и всех операций с
        # интегралами. Состояния хранятся до действия, поэтому Ctrl+Z и
        # Ctrl+Shift+Z не зависят от того, какой именно инструмент был активен.
        self._undo_stack: list[tuple[str, str, dict[str, object]]] = []
        self._redo_stack: list[tuple[str, str, dict[str, object]]] = []
        self._spectrum_dialog_open = False
        self.formula_document = FormulaDocument()
        self.formula_editor = None
        self.formula_status = tk.StringVar(value="Выберите формулу на вкладке «Расчёты».")
        self.preset_library = PresetLibrary(Path(__file__).resolve().parent)
        self.active_preset_key = None
        self._formula_engines = {}
        self.integration_method = SpectrumVariable(self, "integration_method")
        self.integration_display = SpectrumVariable(self, "integration_display")
        # Отдельная переменная нужна виджетам выбора: радиокнопка изменяет
        # свою переменную до callback, а для истории нам важно ещё знать
        # прежний режим расчёта.
        self.integration_choice = tk.StringVar(value=self.integration_method.get())
        self.integration_display_choice = tk.StringVar(value=self.integration_display.get())
        self.build_application_menu()
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.spectrum_tab = tk.Frame(self.notebook)
        self.calculations_tab = tk.Frame(self.notebook)
        self.notebook.add(self.spectrum_tab, text="Спектр и интегралы")
        self.notebook.add(self.calculations_tab, text="Расчёты")
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self.ppm: np.ndarray | None = None
        self.real: np.ndarray | None = None
        self.imaginary: np.ndarray | None = None
        self.spectral_width_hz = 0.0
        self.phase0 = self.phase1 = 0.0
        # Внутренняя точка отсчёта фазы фиксирована. Красная линия в диалоге
        # служит только для удобной ручной настройки φ₀ и не меняет формулу.
        self.phase_pivot = 0.5
        self.phase_reference: int | None = None
        self.corrected: np.ndarray | None = None
        self._title = "NMR"
        self.integration_mode = False
        self.x_zoom_mode = False
        self.integrals: list[dict[str, float | int | str]] = []
        self.reference_integral_id: int | None = None
        self._next_integral_id = 1
        self.integral_colors = ("#e45756", "#4c78a8", "#59a14f", "#f28e2b", "#b279a2", "#76b7b2")
        # Legacy dialog attributes are live views of the selected library record.
        # There is no additional working copy to save back during drawing.
        self._next_spectrum_id = 1
        self.active_spectrum_id: int | None = None
        self.comparisons: dict[int, dict[str, object]] = {}
        self._next_comparison_id = 1
        self.current_comparison_id: int | None = None
        self._comparison_axes: list[object] = []
        self._library_items: list[tuple[str, int] | None] = []

        controls = tk.Frame(self.spectrum_tab, padx=10, pady=8)
        controls.pack(fill="x")
        tk.Button(controls, text="Добавить папки…", command=self.choose_folder).pack(side="left")
        tk.Label(controls, text="Инструменты:").pack(side="left", padx=(12, 3))
        phase_tool = tk.Button(controls, text="φ", width=3, command=self.open_phase_dialog)
        phase_tool.pack(side="left", padx=1)
        self.add_formula_tooltip(phase_tool, "Фазирование спектра")
        baseline_tool = tk.Button(controls, text="B", width=3, command=self.open_correction_dialog)
        baseline_tool.pack(side="left", padx=1)
        self.add_formula_tooltip(baseline_tool, "Коррекция базовой линии")
        self.integration_button = tk.Button(
            controls, text="∫", width=3, command=self.toggle_integration
        )
        self.integration_button.pack(side="left", padx=1)
        self.add_formula_tooltip(self.integration_button, "Режим интегрирования")
        self.integral_bounds_button = tk.Button(
            controls, text="↔∫", width=3, command=self.toggle_integral_boundaries
        )
        self.integral_bounds_button.pack(side="left", padx=1)
        self.add_formula_tooltip(
            self.integral_bounds_button,
            "Изменить границы интегралов: перетащите вертикальные линии",
        )
        self.zoom_button = tk.Button(controls, text="🔍 Выбрать X", command=self.toggle_x_zoom)
        self.zoom_button.pack(side="left", padx=5)
        tk.Button(controls, text="Сбросить ширину", command=self.reset_x_zoom).pack(side="left")
        tk.Button(controls, text="Расчёты…", command=self.open_calculations).pack(
            side="right", padx=4
        )
        self.status = tk.StringVar(value="Выберите папку с обработанным Bruker-спектром.")
        tk.Label(self.spectrum_tab, textvariable=self.status, anchor="w", padx=10).pack(fill="x")

        # Разделитель можно перетаскивать мышью: это важнее фиксированной
        # высоты на ноутбуках с разным разрешением экрана.
        body = tk.PanedWindow(
            self.spectrum_tab,
            orient=tk.HORIZONTAL,
            sashrelief="raised",
            sashwidth=6,
            showhandle=True,
            bd=0,
            relief="flat",
        )
        body.pack(fill="both", expand=True)
        self.library_split = body
        library = tk.Frame(body, padx=4, pady=6)
        self.library_panel = library
        analysis = tk.Frame(body)
        self.analysis_panel = analysis
        body.add(library, minsize=230, width=330)
        body.add(analysis, minsize=620)
        self.spectrum_library = SpectrumLibrary(
            library, self.refresh_spectrum_library, self.on_spectrum_selected
        )
        self.spectrum_library.pack(fill="both", expand=True)
        self.spectrum_list = self.spectrum_library.tree
        self.enable_spectrum_drop(library)

        self.spectrum_split = tk.PanedWindow(
            analysis,
            orient=tk.VERTICAL,
            sashrelief="raised",
            sashwidth=7,
            showhandle=True,
            bd=0,
            relief="flat",
        )
        self.spectrum_split.pack(fill="both", expand=True)
        self.plot_frame = tk.Frame(self.spectrum_split)
        # Размер Matplotlib-холста не задаёт минимальную высоту панели:
        # разделитель должен уметь сжать график на маленьком экране.
        self.plot_frame.pack_propagate(False)
        self.spectrum_split.add(self.plot_frame, minsize=180)
        self._integral_table_visible = False
        self.graph_height_percent = self._view_preferences["graph_percent"]
        self.figure = Figure(figsize=(9, 5), dpi=100)
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("scroll_event", self.zoom_y)
        self.canvas.mpl_connect("button_press_event", self.on_plot_click)
        self.span: SpanSelector | None = None
        self.create_span_selector()
        self.integral_boundary_editor = IntegralBoundaryEditor(self, self.integral_bounds_button)
        self.bind("<FocusIn>", lambda _event: self.restore_interaction_mode())
        # У Frame в Tk для Windows padding должен быть одним числом;
        # асимметричные отступы допустимы только в pack/grid.
        self.table_frame = tk.Frame(self.spectrum_split, padx=10, pady=0)
        table_controls = tk.Frame(self.table_frame)
        table_controls.pack(fill="x", pady=(4, 0))
        tk.Label(table_controls, text="Интегралы").pack(side="left")
        tk.Label(table_controls, text="Вывод:").pack(side="right", padx=(8, 3))
        display_box = ttk.Combobox(
            table_controls,
            textvariable=self.integration_display_choice,
            state="readonly",
            width=31,
            values=self.INTEGRATION_DISPLAYS,
        )
        display_box.pack(side="right")
        display_box.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.change_integration_display(self.integration_display_choice.get()),
        )
        tk.Label(table_controls, text="Метод:").pack(side="right", padx=(12, 3))
        integration_box = ttk.Combobox(
            table_controls,
            textvariable=self.integration_choice,
            state="readonly",
            width=29,
            values=self.INTEGRATION_METHODS,
        )
        integration_box.pack(side="right")
        integration_box.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.change_integration_method(self.integration_choice.get()),
        )
        columns = ("number", "name", "limits", "area", "relative")
        self.integral_table = ttk.Treeview(
            self.table_frame, columns=columns, show="headings", height=5
        )
        headings = {
            "number": "№",
            "name": "Название",
            "limits": "Пределы, ppm",
            "area": "Площадь",
            "relative": "Отн. площадь",
        }
        widths = {"number": 45, "name": 140, "limits": 180, "area": 160, "relative": 130}
        for column in columns:
            self.integral_table.heading(column, text=headings[column])
            self.integral_table.column(column, width=widths[column], anchor="center")
        self.integral_table.pack(fill="both", expand=True)
        self.integral_table.bind("<Button-3>", self.integral_table_menu)
        self.integral_table.bind("<Control-KeyPress>", self.copy_integral_table_shortcut)
        self.calculations_view = CalculationsView(self.calculations_tab, self)
        self.refresh_formula_workspace()
        self.library_split.bind(
            "<ButtonRelease-1>", lambda _event: self.after_idle(self.capture_view_layout), add="+"
        )
        self.spectrum_split.bind(
            "<ButtonRelease-1>", lambda _event: self.after_idle(self.capture_view_layout), add="+"
        )

    def _integral_table_context(self):
        return (
            ("comparison", self.current_comparison_id)
            if self.current_comparison_id is not None
            else ("spectrum", self.active_spectrum_id)
        )

    def _table_view_before_refresh(self):
        context = self._integral_table_context()
        if self._last_integral_table_context == context:
            self._integral_table_views[context] = capture_table_view(self.integral_table)
        self._last_integral_table_context = context
        self._integral_table_refreshing = True
        return self._integral_table_views.get(context, {})

    def _table_view_after_refresh(self, state):
        restore_table_view(self.integral_table, state)
        self._integral_table_refreshing = False

    def _remember_plot_view(self):
        if self.ppm is None:
            return None
        context = self._integral_table_context()
        comparison = self.comparison_record()
        view = {
            "xlim": self.axes.get_xlim(),
            "ylim": self.axes.get_ylim(),
            "stacked": bool(comparison and comparison.get("mode") == "separate"),
        }
        self._plot_views[context] = view
        if self._last_integral_table_context == context:
            self._integral_table_views[context] = capture_table_view(self.integral_table)
        return view

    def _restore_plot_view(self, previous):
        remembered = self._plot_views.get(self._integral_table_context(), {})
        comparison = self.comparison_record()
        if comparison:
            offsets = comparison.get("offsets", {})
            domains = []
            for sid in comparison.get("spectrum_ids", []):
                record = self.spectra.get(int(sid))
                if record is not None:
                    offset = float(offsets.get(sid, offsets.get(str(sid), 0)))
                    ppm = np.asarray(record["ppm"])
                    domains.extend((float(ppm.min()) + offset, float(ppm.max()) + offset))
            domain = (min(domains), max(domains)) if domains else None
        else:
            domain = (
                (float(self.ppm.min()), float(self.ppm.max())) if self.ppm is not None else None
            )
        source = previous if self.linked_ppm_view.get() and previous else remembered
        limits = usable_limits(source.get("xlim") if source else None, domain)
        if limits:
            self.axes.set_xlim(limits)
        if self.auto_y_on_switch.get():
            self.fit_current_view_y()
        elif not comparison or comparison.get("mode") != "separate":
            # Without autoscaling, retain a common Y scale only between compatible
            # plots; a normalized stacked scale is never an intensity scale.
            source_y = remembered.get("ylim")
            if not source_y and previous and not previous.get("stacked"):
                source_y = previous.get("ylim")
            limits_y = usable_limits(source_y)
            if limits_y:
                self.axes.set_ylim(limits_y)
        self.canvas.draw_idle()

    def fit_current_view_y(self):
        """Scale only visible data curves, excluding vertical domain markers."""
        comparison = self.comparison_record()
        if comparison and comparison.get("mode") == "separate":
            return
        if callable(getattr(self, "fit_visible_plot_y", None)):
            self.fit_visible_plot_y()
            return
        low, high = sorted(self.axes.get_xlim())
        values = []
        for artist in self.axes.lines:
            if not artist.get_visible() or artist.get_transform() is not self.axes.transData:
                continue
            x, y = np.asarray(artist.get_xdata()), np.asarray(artist.get_ydata())
            if x.size < 3 or x.shape != y.shape:
                continue
            mask = np.isfinite(y) & (x >= low) & (x <= high)
            if np.any(mask):
                values.append(y[mask])
        if values:
            low_y, high_y = min(float(v.min()) for v in values), max(float(v.max()) for v in values)
            margin = max((high_y - low_y) * 0.06, np.finfo(float).eps)
            self.axes.set_ylim(low_y - margin, high_y + margin)
            self.canvas.draw_idle()

    def capture_view_layout(self):
        preferences = self._view_preferences
        if self.winfo_width() > 1 and self.winfo_height() > 1:
            preferences["geometry"] = self.geometry()
            preferences["maximized"] = self.state() == "zoomed"
        if self.library_visible.get() and self.library_split.winfo_width() > 1:
            try:
                preferences["library_fraction"] = (
                    self.library_split.sash_coord(0)[0] / self.library_split.winfo_width()
                )
            except tk.TclError:
                pass
        if self._integral_table_visible and self.spectrum_split.winfo_height() > 250:
            try:
                self.graph_height_percent = min(
                    85,
                    max(
                        30,
                        self.spectrum_split.sash_coord(0)[1]
                        * 100
                        / self.spectrum_split.winfo_height(),
                    ),
                )
            except tk.TclError:
                pass
        preferences.update(
            linked_ppm=self.linked_ppm_view.get(),
            auto_y=self.auto_y_on_switch.get(),
            library_visible=self.library_visible.get(),
            table_visible=self._integral_table_visible,
            graph_percent=self.graph_height_percent,
        )
        return dict(preferences)

    def restore_view_preferences(self):
        preferences = self._view_preferences
        if preferences.get("geometry"):
            self.geometry(
                visible_geometry(
                    preferences["geometry"], (self.winfo_screenwidth(), self.winfo_screenheight())
                )
            )
        if preferences.get("maximized"):
            try:
                self.state("zoomed")
            except tk.TclError:
                pass
        self.update_idletasks()
        self.toggle_library_panel()
        self.toggle_integral_table_panel()

    def toggle_library_panel(self):
        panes = tuple(map(str, self.library_split.panes()))
        if self.library_visible.get():
            if str(self.library_panel) not in panes:
                self.library_split.add(
                    self.library_panel, before=self.analysis_panel, minsize=230, width=330
                )
            self.update_idletasks()
            width = self.library_split.winfo_width()
            if width > 1:
                self.library_split.sash_place(
                    0, int(width * self._view_preferences["library_fraction"]), 0
                )
        elif str(self.library_panel) in panes:
            self.library_split.forget(self.library_panel)

    def toggle_integral_table_panel(self):
        if self.integral_table_visible.get():
            self.show_integral_table()
        else:
            self.hide_integral_table()

    def destroy(self):
        try:
            write_preferences(self.capture_view_layout())
        except tk.TclError:
            pass
        super().destroy()

    def build_application_menu(self) -> None:
        """Основное меню проекта и команды обработки сигналов."""
        menu_bar = tk.Menu(self)
        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="Добавить папки…", command=self.choose_folder)
        file_menu.add_separator()
        file_menu.add_command(
            label="Открыть проект…", accelerator="Ctrl+O", command=self.open_project
        )
        file_menu.add_separator()
        file_menu.add_command(label="Сохранить", accelerator="Ctrl+S", command=self.save_project)
        file_menu.add_command(
            label="Сохранить как…", accelerator="Ctrl+Shift+S", command=self.save_project_as
        )
        menu_bar.add_cascade(label="Файл", menu=file_menu)
        edit_menu = tk.Menu(menu_bar, tearoff=False)
        edit_menu.add_command(label="Отменить", accelerator="Ctrl+Z", command=self.undo_last_action)
        edit_menu.add_command(
            label="Повторить", accelerator="Ctrl+Shift+Z", command=self.redo_last_action
        )
        menu_bar.add_cascade(label="Правка", menu=edit_menu)
        settings_menu = tk.Menu(menu_bar, tearoff=False)
        integration_menu = tk.Menu(settings_menu, tearoff=False)
        for method in self.INTEGRATION_METHODS:
            integration_menu.add_radiobutton(
                label=method,
                variable=self.integration_choice,
                value=method,
                command=lambda value=method: self.change_integration_method(value),
            )
        settings_menu.add_cascade(label="Метод интегрирования", menu=integration_menu)
        display_menu = tk.Menu(settings_menu, tearoff=False)
        for display in self.INTEGRATION_DISPLAYS:
            display_menu.add_radiobutton(
                label=display,
                variable=self.integration_display_choice,
                value=display,
                command=lambda value=display: self.change_integration_display(value),
            )
        settings_menu.add_cascade(label="Вывод значений интегралов", menu=display_menu)
        view_menu = tk.Menu(settings_menu, tearoff=False)
        view_menu.add_checkbutton(
            label="Одинаковый диапазон ppm при переключении",
            variable=self.linked_ppm_view,
        )
        view_menu.add_checkbutton(
            label="Автомасштаб Y для каждого спектра",
            variable=self.auto_y_on_switch,
        )
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="Библиотека спектров",
            variable=self.library_visible,
            command=self.toggle_library_panel,
        )
        view_menu.add_checkbutton(
            label="Таблица интегралов",
            variable=self.integral_table_visible,
            command=self.toggle_integral_table_panel,
        )
        view_menu.add_command(label="Автомасштаб Y сейчас", command=self.fit_current_view_y)
        settings_menu.add_cascade(label="Просмотр спектров", menu=view_menu)
        menu_bar.add_cascade(label="Настройки", menu=settings_menu)
        processing_menu = tk.Menu(menu_bar, tearoff=False)
        processing_menu.add_command(label="Фазировать", command=self.open_phase_dialog)
        processing_menu.add_command(
            label="Скорректировать базовую линию", command=self.open_correction_dialog
        )
        processing_menu.add_separator()
        processing_menu.add_command(label="Интегрирование", command=self.toggle_integration)
        processing_menu.add_command(
            label="Изменить границы интегралов", command=self.toggle_integral_boundaries
        )
        processing_menu.add_command(label="Вывести несколько…", command=self.open_comparison_dialog)
        processing_menu.add_command(
            label="Автоматически выровнять по X", command=self.auto_align_comparison
        )
        processing_menu.add_command(
            label="Сдвинуть спектр по X…", command=self.shift_comparison_spectrum
        )
        menu_bar.add_cascade(label="Обработка сигналов", menu=processing_menu)
        calculation_menu = tk.Menu(menu_bar, tearoff=False)
        calculation_menu.add_command(label="Формулы и результаты", command=self.open_calculations)
        calculation_menu.add_command(label="Редактор формул…", command=self.open_formula_editor)
        calculation_menu.add_command(label="Загрузить формулу…", command=self.load_formula_preset)
        menu_bar.add_cascade(label="Расчёты", menu=calculation_menu)
        self.configure(menu=menu_bar)
        self.bind_all("<Control-o>", lambda _event: self.open_project())
        self.bind_all("<Control-s>", lambda _event: self.save_project())
        self.bind_all("<Control-Shift-S>", lambda _event: self.save_project_as())
        # На русской раскладке Windows физическая клавиша Z приходит в Tk
        # как «я»/Cyrillic_ya и шаблон <Control-z> не совпадает. Общий
        # обработчик смотрит также на физический virtual key 90, поэтому
        # Ctrl+Z и Ctrl+Shift+Z работают независимо от раскладки и фокуса.
        self.bind_all("<Control-KeyPress>", self.handle_control_shortcut, add="+")

    def handle_control_shortcut(self, event) -> str | None:
        """Обработать Undo/Redo для латинской и русской раскладки клавиатуры."""
        keysym = str(getattr(event, "keysym", "")).lower()
        is_z_key = keysym in {"z", "cyrillic_ya", "я"} or int(getattr(event, "keycode", -1)) == 90
        if not is_z_key:
            return None
        if int(getattr(event, "state", 0)) & 0x0001:  # Shift
            return self.redo_last_action(event)
        return self.undo_last_action(event)

    def choose_folder(self) -> None:
        """Стандартный Windows-проводник с множественным выбором папок."""
        folders = choose_windows_folders(self.winfo_id())
        if folders is None:
            # Редкий fallback для не-Windows окружения: Tk умеет только одну
            # папку, но приложение всё равно остаётся запускаемым.
            folder = filedialog.askdirectory(parent=self, title="Папка Bruker-эксперимента")
            folders = (folder,) if folder else ()
        if not folders:
            return
        self.add_spectrum_paths(folders)

    @staticmethod
    def spectrum_path_from_drop(path: str | Path) -> Path:
        """Получить папку, которую понимает загрузчик, из файла или каталога."""
        source = Path(path)
        return source.parent if source.is_file() else source

    def add_spectrum_paths(self, paths: tuple[str, ...] | list[str]) -> None:
        """Добавить несколько файлов 1r/папок, не прерываясь на одном сбое."""
        added = 0
        failed: list[str] = []
        for path in paths:
            before = len(self.spectra)
            try:
                success = self.add_spectrum_folder(
                    self.spectrum_path_from_drop(path), show_error=False
                )
            except (OSError, ValueError):
                success = False
            if success and len(self.spectra) > before:
                added += 1
            elif not success:
                failed.append(Path(path).name)
        if added:
            self.status.set(f"Добавлено спектров: {added}. Выберите нужный слева.")
        if failed:
            messagebox.showwarning(
                "Добавление спектров",
                "Не удалось прочитать:\n" + "\n".join(failed[:8]),
                parent=self,
            )

    def enable_spectrum_drop(self, library: tk.Widget) -> None:
        """Разрешить перетаскивание из Проводника на левую библиотеку."""
        if not DND_ENABLED:
            return
        for widget in (library, self.spectrum_list):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind(
                "<<DropEnter>>", lambda _event: self.spectrum_list.set_drop_active(True)
            )
            widget.dnd_bind(
                "<<DropLeave>>", lambda _event: self.spectrum_list.set_drop_active(False)
            )
            widget.dnd_bind("<<Drop>>", self.on_spectrum_drop)

    def on_spectrum_drop(self, event):
        """Обработчик набора путей, переданного Windows через tkinterdnd2."""
        self.spectrum_list.set_drop_active(False)
        try:
            paths = tuple(str(path) for path in self.tk.splitlist(event.data))
        except tk.TclError:
            paths = (str(event.data),)
        self.add_spectrum_paths(paths)
        return getattr(event, "action", "copy")

    def clear_formula_workspace(self) -> None:
        """Очистить формулы только при открытии нового проекта, не при добавлении спектра."""
        self.formula_document.clear()
        self.active_preset_key = None
        if self.formula_editor is not None:
            self.formula_editor.reset_interaction()
        self.refresh_formula_workspace()

    def save_active_spectrum(self) -> None:
        """Refresh Tk projections; numerical state is already in the library."""
        self.integration_method.sync()
        self.integration_display.sync()
        self.sync_integration_method_choice()

    def restore_active_spectrum(self, spectrum_id: int) -> None:
        record = self.spectra[spectrum_id]
        self.active_spectrum_id = spectrum_id
        self.integration_method.set(
            self.normalize_integration_method(
                str(record.get("integration_method", "Обычная площадь"))
            )
        )
        display = str(record.get("integration_display", "Площадь"))
        self.integration_display.set(display if display in self.INTEGRATION_DISPLAYS else "Площадь")
        self.sync_integration_method_choice()

    def refresh_spectrum_library(self) -> None:
        """Обновить левую библиотеку спектров и созданных сравнений."""
        if not hasattr(self, "spectrum_list"):
            return
        library = self.spectrum_library
        library.update_batch(
            getattr(self, "batch_session", None),
            on_next=getattr(self, "batch_next_attention", None),
        )
        query = self.spectrum_library.search.get()
        rows = []
        self._library_items = []
        statuses = {}
        for spectrum_id, record in self.spectra.items():
            if not matches_search(record, query) or not library.accepts(spectrum_id):
                continue
            status = statuses[spectrum_id] = library_status(record)
            summary, tag = library.decorate(spectrum_id, status.summary, bool(status.issues))
            rows.append((spectrum_label(record), summary, tag))
            self._library_items.append(("spectrum", spectrum_id))
        visible_spectra = len(rows)
        visible_comparisons = 0
        for comparison_id, comparison in self.comparisons.items():
            if not library.show_comparisons:
                break
            if not matches_search(comparison, query) and not any(
                matches_search(self.spectra[sid], query)
                for sid in comparison.get("spectrum_ids", [])
                if sid in self.spectra
            ):
                continue
            if not visible_comparisons:
                rows.append(("Сравнения", "", "section"))
                self._library_items.append(None)
            rows.append(
                (f"▦  {comparison['name']}", f"Спектров: {len(comparison['spectrum_ids'])}", "")
            )
            self._library_items.append(("comparison", comparison_id))
            visible_comparisons += 1
        self.spectrum_list.set_rows(rows)
        self.spectrum_library.count.set(
            f"Спектры: {visible_spectra} / {len(self.spectra)}"
            + (f" · сравнений: {visible_comparisons}" if visible_comparisons else "")
        )
        selected_item: tuple[str, int] | None = (
            ("comparison", self.current_comparison_id)
            if self.current_comparison_id is not None
            else ("spectrum", self.active_spectrum_id)
            if self.active_spectrum_id is not None
            else None
        )
        if selected_item in self._library_items:
            position = self._library_items.index(selected_item)
            if self.spectrum_list.curselection() != (position,):
                self.spectrum_list.selection_set(position)
            self.spectrum_list.activate(position)
        else:
            self.spectrum_list.selection_clear()
        if self.current_comparison_id in self.comparisons:
            comparison = self.comparisons[self.current_comparison_id]
            names = [
                str(self.spectra[sid]["name"])
                for sid in comparison["spectrum_ids"]
                if sid in self.spectra
            ]
            self.spectrum_library.set_details(f"{comparison['name']}\nСпектры: {', '.join(names)}")
        elif self.active_spectrum_id in self.spectra:
            record = self.spectra[self.active_spectrum_id]
            batch_details = library.batch_details(self.active_spectrum_id)
            self.spectrum_library.set_details(
                (batch_details + "\n" if batch_details else "")
                + spectrum_details(record, statuses.get(self.active_spectrum_id))
            )
        elif query:
            self.spectrum_library.set_details(
                "Ничего не найдено. Измените запрос или очистите поиск кнопкой ×."
            )

    def on_spectrum_selected(self, _event=None) -> None:
        selected = self.spectrum_list.curselection()
        if not selected:
            return
        if selected[0] >= len(self._library_items):
            return
        item = self._library_items[selected[0]]
        if item is None:
            self.spectrum_list.selection_clear(0, "end")
            return
        kind, item_id = item
        if kind == "spectrum":
            if item_id == self.active_spectrum_id and self.current_comparison_id is None:
                return
            self.activate_spectrum(item_id)
        elif item_id in self.comparisons:
            if item_id == self.current_comparison_id:
                return
            self.integral_boundary_editor.set_active(False)
            previous_view = self._remember_plot_view()
            self.save_active_spectrum()
            self.current_comparison_id = item_id
            self.clear_history(preserve_formulas=True)
            self.refresh_spectrum_library()
            self.draw_comparison(reset_x=True, reset_y=True)
            self._restore_plot_view(previous_view)
            self.status.set(f"Открыто {self.comparisons[item_id]['name']}.")

    def activate_spectrum(self, spectrum_id: int) -> None:
        """Открыть один спектр из библиотеки и выйти из обзорного графика."""
        if spectrum_id not in self.spectra:
            return
        previous_view = self._remember_plot_view()
        self.integral_boundary_editor.set_active(False)
        self.save_active_spectrum()
        self.current_comparison_id = None
        self._comparison_axes = []
        self.restore_active_spectrum(spectrum_id)
        self.clear_history(preserve_formulas=True)
        self.refresh_spectrum_library()
        self.draw(reset_x=True, reset_y=True)
        self._restore_plot_view(previous_view)
        self.status.set(f"Открыт спектр: {self.spectra[spectrum_id]['name']}.")

    def add_spectrum_folder(
        self,
        folder: str | Path,
        *,
        clear_formulas: bool = False,
        reset_project: bool = True,
        show_error: bool = True,
    ) -> bool:
        """Добавить Bruker-спектр в библиотеку, не сбрасывая остальные."""
        try:
            ppm, real, imaginary, spectral_width_hz, source = load_bruker_spectrum(folder)
        except (OSError, ValueError) as error:
            if show_error:
                messagebox.showerror("Ошибка загрузки", str(error), parent=self)
            return False
        # Канонический адрес — папка именно выбранной обработки pdata/N.
        # Поэтому один и тот же спектр не добавится дважды, если сначала
        # выбрали папку эксперимента, а затем перетащили файл 1r.
        source_folder = source.parent.resolve()
        existing = next(
            (
                item_id
                for item_id, item in self.spectra.items()
                if item.get("source_folder") == source_folder
            ),
            None,
        )
        if existing is not None:
            self.activate_spectrum(existing)
            self.status.set("Этот спектр уже есть в списке — он открыт.")
            return True
        if clear_formulas:
            self.clear_formula_workspace()
        spectrum_id = self._next_spectrum_id
        self._next_spectrum_id += 1
        experiment_folder = (
            source_folder.parent.parent if source_folder.parent.name == "pdata" else source_folder
        )
        base_name = experiment_folder.name or source_folder.name or f"Спектр {spectrum_id}"
        used_names = {str(item["name"]) for item in self.spectra.values()}
        name, suffix = base_name, 2
        while name in used_names:
            name = f"{base_name} ({suffix})"
            suffix += 1
        self.spectra[spectrum_id] = {
            "id": spectrum_id,
            "name": name,
            "source_folder": source_folder,
            "bruker_title": read_bruker_title(source_folder),
            "ppm": ppm,
            "real": real,
            "imaginary": imaginary,
            "spectral_width_hz": float(spectral_width_hz),
            "ppm_shift": 0.0,
            "phase0": 0.0,
            "phase1": 0.0,
            "phase_pivot": 0.5,
            "phase_reference": None,
            "corrected": None,
            "integrals": [],
            "reference_integral_id": None,
            "next_integral_id": 1,
            "integration_method": "Обычная площадь",
            "integration_display": "Площадь",
            "title": f"NMR: {source.parent}",
        }
        if reset_project:
            self.project_path = None
        self.activate_spectrum(spectrum_id)
        self.status.set(f"Добавлен спектр «{name}». Выберите его слева для отдельной обработки.")
        return True

    def load_spectrum_folder(self, folder: str | Path) -> bool:
        """Открыть одиночный спектр: используется при загрузке старого проекта."""
        self.integral_boundary_editor.set_active(False)
        self._plot_views.clear()
        self._integral_table_views.clear()
        self._last_integral_table_context = None
        self.spectra.clear()
        self.comparisons.clear()
        self.active_spectrum_id = None
        self.current_comparison_id = None
        self._next_spectrum_id = 1
        self._next_comparison_id = 1
        self.clear_history()
        return self.add_spectrum_folder(folder, clear_formulas=True, reset_project=False)

    def capture_spectrum_state(self) -> dict[str, object]:
        """Полный снимок изменяемого состояния активного спектра.

        Интегралы входят в тот же снимок, что фаза и линия: пользователь может
        подряд фазировать, добавить область и удалить другую, а затем идти по
        этой единой последовательности Ctrl+Z / Ctrl+Shift+Z.
        """
        return self.spectra.current.snapshot()

    def spectrum_state_changed(self, previous: dict[str, object]) -> bool:
        """Есть ли отличие текущего спектра от сохранённого снимка."""
        return not same_state(previous, self.capture_spectrum_state())

    def clear_history(self, *, preserve_formulas: bool = False) -> None:
        """Очистить историю спектра; при переключении строк сохранить историю схемы."""
        if preserve_formulas:
            self._undo_stack[:] = [item for item in self._undo_stack if item[0] == "formula"]
            self._redo_stack[:] = [item for item in self._redo_stack if item[0] == "formula"]
            return
        self._undo_stack.clear()
        self._redo_stack.clear()

    def trim_history(self) -> None:
        if len(self._undo_stack) > 20:
            del self._undo_stack[:-20]
        if len(self._redo_stack) > 20:
            del self._redo_stack[:-20]

    def remember_spectrum_state(self, previous: dict[str, object], action: str) -> None:
        """Добавить состояние до действия в историю и очистить ветку redo."""
        if not self.spectrum_state_changed(previous):
            return
        self._undo_stack.append(("spectrum", action, previous))
        self._redo_stack.clear()
        self.trim_history()

    def remember_comparison_integrals(self, comparison: dict[str, object], action: str) -> None:
        """Сохранить общие интегралы обзора для Ctrl+Z.

        В режиме отдельных осей один диапазон принадлежит самому сравнению,
        а не активному спектру. Поэтому снимок `self.integrals` здесь был бы
        пустым и отмена ранее ничего не делала.
        """
        comparison_id = comparison.get("id", self.current_comparison_id)
        if not isinstance(comparison_id, int):
            return
        previous = {
            "comparison_id": comparison_id,
            "integrals": [dict(item) for item in comparison.get("integrals", [])],
            "next_integral_id": int(comparison.get("next_integral_id", 1)),
        }
        self._undo_stack.append(("comparison_integrals", action, previous))
        self._redo_stack.clear()
        self.trim_history()

    def remember_comparison_offsets(self, comparison: dict[str, object], action: str) -> None:
        """Сохранить X-сдвиги сравнения, чтобы автовыравнивание отменялось через Ctrl+Z."""
        comparison_id = comparison.get("id", self.current_comparison_id)
        if not isinstance(comparison_id, int):
            return
        previous = {
            "comparison_id": comparison_id,
            "offsets": dict(comparison.get("offsets", {})),
        }
        self._undo_stack.append(("comparison_offsets", action, previous))
        self._redo_stack.clear()
        self.trim_history()

    def capture_formula_state(self) -> dict[str, object]:
        """Снимок схемы формулы для общей истории Ctrl+Z / Ctrl+Shift+Z.

        Формулы не зависят от выбранного спектра, поэтому их нельзя хранить в
        снимке спектра. Отдельный снимок также сохраняет ручные маршруты
        проводов, комментарии и фреймы — отмена возвращает именно ту схему,
        которую видел пользователь.
        """
        return {"document": self.formula_document.snapshot(), "preset_key": self.active_preset_key}

    def formula_state_changed(self, previous: dict[str, object]) -> bool:
        return previous != self.capture_formula_state()

    def remember_formula_state(
        self, previous: dict[str, object], action: str, *, preserve_preset=False
    ) -> None:
        """Добавить одно законченное действие редактора формул в историю."""
        if not self.formula_state_changed(previous):
            return
        if not preserve_preset:
            self.active_preset_key = None
        self._undo_stack.append(("formula", action, previous))
        self._redo_stack.clear()
        self.trim_history()
        self.refresh_calculations()

    def restore_formula_state(self, state: dict[str, object]) -> bool:
        try:
            if not isinstance(state.get("document"), dict):
                return False
            self.formula_document.restore(state["document"])
            self.active_preset_key = state.get("preset_key")
            if self.formula_editor is not None:
                self.formula_editor.reset_interaction()
        except (KeyError, TypeError, ValueError):
            return False
        self.refresh_formula_workspace()
        return True

    @staticmethod
    def comparison_integral_state(comparison: dict[str, object]) -> dict[str, object]:
        return {
            "comparison_id": comparison.get("id"),
            "integrals": [dict(item) for item in comparison.get("integrals", [])],
            "next_integral_id": int(comparison.get("next_integral_id", 1)),
        }

    @staticmethod
    def comparison_offset_state(comparison: dict[str, object]) -> dict[str, object]:
        return {
            "comparison_id": comparison.get("id"),
            "offsets": dict(comparison.get("offsets", {})),
        }

    def restore_comparison_integral_state(self, state: dict[str, object]) -> bool:
        comparison_id = state.get("comparison_id")
        comparison = self.comparisons.get(comparison_id) if isinstance(comparison_id, int) else None
        if comparison is None:
            return False
        comparison["integrals"] = [dict(item) for item in state.get("integrals", [])]
        comparison["next_integral_id"] = int(state.get("next_integral_id", 1))
        return True

    def restore_comparison_offset_state(self, state: dict[str, object]) -> bool:
        comparison_id = state.get("comparison_id")
        comparison = self.comparisons.get(comparison_id) if isinstance(comparison_id, int) else None
        offsets = state.get("offsets")
        if comparison is None or not isinstance(offsets, dict):
            return False
        comparison["offsets"] = dict(offsets)
        return True

    def restore_spectrum_state(self, state: dict[str, object]) -> None:
        self.spectra.current.restore(state)
        self.integration_method.set(
            self.normalize_integration_method(
                str(state.get("integration_method", "Обычная площадь"))
            )
        )
        display = str(state.get("integration_display", "Площадь"))
        self.integration_display.set(display if display in self.INTEGRATION_DISPLAYS else "Площадь")
        self.sync_integration_method_choice()

    def current_history_state(
        self, kind: str, target: dict[str, object]
    ) -> dict[str, object] | None:
        if kind == "spectrum":
            return self.capture_spectrum_state()
        if kind == "comparison_integrals":
            comparison_id = target.get("comparison_id")
            comparison = (
                self.comparisons.get(comparison_id) if isinstance(comparison_id, int) else None
            )
            return self.comparison_integral_state(comparison) if comparison is not None else None
        if kind == "comparison_offsets":
            comparison_id = target.get("comparison_id")
            comparison = (
                self.comparisons.get(comparison_id) if isinstance(comparison_id, int) else None
            )
            return self.comparison_offset_state(comparison) if comparison is not None else None
        if kind == "formula":
            return self.capture_formula_state()
        return None

    def apply_history_state(self, kind: str, state: dict[str, object]) -> bool:
        if kind == "spectrum":
            self.restore_spectrum_state(state)
            self.draw()
            return True
        if kind == "comparison_integrals":
            if not self.restore_comparison_integral_state(state):
                return False
            if self.current_comparison_id == state.get("comparison_id"):
                self.draw_comparison()
            return True
        if kind == "comparison_offsets":
            if not self.restore_comparison_offset_state(state):
                return False
            if self.current_comparison_id == state.get("comparison_id"):
                self.draw_comparison()
            return True
        if kind == "formula":
            return self.restore_formula_state(state)
        return False

    def undo_last_action(self, _event: object | None = None) -> str:
        """Отменить последнее действие над спектром, сравнением или схемой формулы."""
        if self._spectrum_dialog_open:
            return "break"
        if self.integral_boundary_editor.cancel_drag():
            self.status.set("Перетаскивание границы отменено.")
            return "break"
        if not self._undo_stack:
            self.status.set("Нет действий для отмены.")
            return "break"
        kind, action, previous = self._undo_stack.pop()
        current = self.current_history_state(kind, previous)
        if current is None or not self.apply_history_state(kind, previous):
            self.status.set("Нельзя отменить: исходный объект истории уже недоступен.")
            return "break"
        self._redo_stack.append((kind, action, current))
        self.trim_history()
        self.after_idle(self.restore_interaction_mode)
        message = f"Отменено: {action}."
        self.status.set(message)
        if kind == "formula" and hasattr(self, "formula_status"):
            self.formula_status.set(message)
        return "break"

    def redo_last_action(self, _event: object | None = None) -> str:
        """Вернуть действие над спектром, сравнением или схемой формулы."""
        if self._spectrum_dialog_open:
            return "break"
        if self.integral_boundary_editor.cancel_drag():
            self.status.set("Перетаскивание границы отменено.")
            return "break"
        if not self._redo_stack:
            self.status.set("Нет действий для повтора.")
            return "break"
        kind, action, following = self._redo_stack.pop()
        current = self.current_history_state(kind, following)
        if current is None or not self.apply_history_state(kind, following):
            self.status.set("Нельзя повторить: исходный объект истории уже недоступен.")
            return "break"
        self._undo_stack.append((kind, action, current))
        self.trim_history()
        self.after_idle(self.restore_interaction_mode)
        message = f"Повторено: {action}."
        self.status.set(message)
        if kind == "formula" and hasattr(self, "formula_status"):
            self.formula_status.set(message)
        return "break"

    @staticmethod
    def encode_project_signal(signal: np.ndarray | None) -> dict[str, object] | None:
        return encode_signal(signal)

    @staticmethod
    def decode_project_signal(payload: object, expected_size: int) -> np.ndarray | None:
        return decode_signal(payload, expected_size)

    def project_spectrum_data(self, record: dict[str, object]) -> dict[str, object]:
        integrals = [
            {
                "id": int(item["id"]),
                "left": float(item["left"]),
                "right": float(item["right"]),
                "color": str(item["color"]),
                "name": str(item["name"]),
                "number": int(item.get("number", index)),
            }
            for index, item in enumerate(
                sorted(
                    record.get("integrals", []), key=lambda item: int(item.get("number", 10**9))
                ),
                start=1,
            )
        ]
        return {
            "name": str(record["name"]),
            "bruker_title": clean_title(record.get("bruker_title", "")),
            "source_folder": str(record["source_folder"]),
            "phase": {
                "p0": float(record["phase0"]),
                "p1": float(record["phase1"]),
                "reference": record["phase_reference"],
            },
            "ppm_shift": float(record["ppm_shift"]),
            "corrected_signal": self.encode_project_signal(record.get("corrected")),
            "integrals": integrals,
            "reference_integral_id": record.get("reference_integral_id"),
            "integration_method": str(record.get("integration_method", "Обычная площадь")),
            "integration_display": str(record.get("integration_display", "Площадь")),
        }

    def project_data(self) -> dict[str, object]:
        if self.ppm is None or self.source_folder is None:
            raise ValueError("сначала откройте спектр")
        self.save_active_spectrum()
        integrals = [
            {
                "id": int(item["id"]),
                "left": float(item["left"]),
                "right": float(item["right"]),
                "color": str(item["color"]),
                "name": str(item["name"]),
                "number": int(item.get("number", index)),
            }
            for index, item in enumerate(self.ordered_integrals(), start=1)
        ]
        data = {
            "format": "nmr-analysis-project",
            "version": 2,
            "source_folder": str(self.source_folder),
            "phase": {"p0": self.phase0, "p1": self.phase1, "reference": self.phase_reference},
            "ppm_shift": self.ppm_shift,
            # Сохраняется уже вычтенный сигнал, поэтому результат коррекции
            # восстанавливается точно, а не пересчитывается с другими настройками.
            "corrected_signal": self.encode_project_signal(self.corrected),
            "integrals": integrals,
            "reference_integral_id": self.reference_integral_id,
            "integration_method": self.integration_method.get(),
            "integration_display": self.integration_display.get(),
            "formula_workspace": self.formula_preset_data(),
            "view": {"xlim": list(self.axes.get_xlim()), "ylim": list(self.axes.get_ylim())},
        }
        if self.spectra:
            spectrum_ids = list(self.spectra)
            data["spectra"] = [
                self.project_spectrum_data(record) for record in self.spectra.values()
            ]
            data["active_spectrum_index"] = (
                spectrum_ids.index(self.active_spectrum_id)
                if self.active_spectrum_id in spectrum_ids
                else 0
            )
            data["comparisons"] = [
                {
                    "name": str(comparison["name"]),
                    "spectrum_indices": [
                        spectrum_ids.index(int(value))
                        for value in comparison.get("spectrum_ids", [])
                        if int(value) in spectrum_ids
                    ],
                    "mode": str(comparison.get("mode", "common")),
                    "offsets": dict(comparison.get("offsets", {})),
                    "offsets_by_index": [
                        float(comparison.get("offsets", {}).get(int(value), 0.0))
                        for value in comparison.get("spectrum_ids", [])
                        if int(value) in spectrum_ids
                    ],
                    "integrals": list(comparison.get("integrals", [])),
                    "next_integral_id": comparison.get("next_integral_id", 1),
                }
                for comparison in self.comparisons.values()
            ]
            comparison_ids = list(self.comparisons)
            data["active_comparison_index"] = (
                comparison_ids.index(self.current_comparison_id)
                if self.current_comparison_id in comparison_ids
                else None
            )
            calculation_comparison = self.calculation_comparison_record()
            data["calculation_comparison_index"] = (
                comparison_ids.index(calculation_comparison["id"])
                if calculation_comparison is not None
                and calculation_comparison["id"] in comparison_ids
                else None
            )
        return data

    def save_project(self) -> None:
        if self.project_path is None:
            self.save_project_as()
            return
        self.write_project(self.project_path)

    def save_project_as(self) -> None:
        filename = filedialog.asksaveasfilename(
            parent=self,
            title="Сохранить проект NMR",
            defaultextension=".nmrproj.json",
            filetypes=(("Проект NMR", "*.nmrproj.json"), ("JSON", "*.json"), ("Все файлы", "*.*")),
        )
        if filename:
            self.write_project(Path(filename))

    def write_project(self, filename: Path) -> None:
        try:
            write_project_file(filename, self.project_data())
        except (OSError, ValueError) as error:
            messagebox.showerror(
                "Проект NMR", f"Не удалось сохранить проект:\n{error}", parent=self
            )
            return
        self.project_path = filename
        self.status.set(f"Проект сохранён: {filename.name}")

    def open_project(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self,
            title="Открыть проект NMR",
            filetypes=(("Проект NMR", "*.nmrproj.json *.json"), ("Все файлы", "*.*")),
        )
        if not filename:
            return
        try:
            project = read_project(filename)
            self.prepare_project_load(project)
            source_folder = Path(str(project["source_folder"]))
            if not self.project_source_available(source_folder):
                raise ValueError(f"не найдена папка спектра: {source_folder}")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            messagebox.showerror("Проект NMR", f"Не удалось открыть проект:\n{error}", parent=self)
            return
        try:
            saved_spectra = project.get("spectra")
            payloads = (
                [item for item in saved_spectra if isinstance(item, dict)]
                if isinstance(saved_spectra, list)
                else [project]
            )
            if not payloads:
                raise ValueError("в проекте нет спектров")
            first_source = Path(str(payloads[0].get("source_folder", source_folder)))
            if not self.project_source_available(first_source) or not self.load_spectrum_folder(
                first_source
            ):
                return
            for index, payload in enumerate(payloads):
                payload_source = Path(str(payload.get("source_folder", source_folder)))
                if index:
                    if not self.project_source_available(payload_source):
                        raise ValueError(f"не найдена папка спектра: {payload_source}")
                    if not self.add_spectrum_folder(payload_source, reset_project=False):
                        return
                self.restore_project_spectrum_payload(payload)
                if payload.get("name"):
                    self.spectra[self.active_spectrum_id]["name"] = str(payload["name"])
                self.save_active_spectrum()
            active_index = project.get("active_spectrum_index", 0)
            ids = list(self.spectra)
            if isinstance(active_index, int) and 0 <= active_index < len(ids):
                self.restore_active_spectrum(ids[active_index])
            self.comparisons.clear()
            for raw in project.get("comparisons", []):
                if not isinstance(raw, dict):
                    continue
                selected = [
                    ids[int(item)]
                    for item in raw.get("spectrum_indices", [])
                    if isinstance(item, int) and 0 <= item < len(ids)
                ]
                if len(selected) < 2:
                    continue
                comparison_id = self._next_comparison_id
                self._next_comparison_id += 1
                raw_offsets = raw.get("offsets_by_index", [])
                offsets = {
                    spectrum_id: float(raw_offsets[index])
                    if isinstance(raw_offsets, list) and index < len(raw_offsets)
                    else 0.0
                    for index, spectrum_id in enumerate(selected)
                }
                self.comparisons[comparison_id] = {
                    "id": comparison_id,
                    "name": str(raw.get("name") or f"Сравнение {comparison_id}"),
                    "spectrum_ids": selected,
                    "mode": str(raw.get("mode") or "common"),
                    "offsets": offsets,
                    "integrals": list(raw.get("integrals", [])),
                    "next_integral_id": int(raw.get("next_integral_id", 1)),
                }
            workspace = project.get("formula_workspace")
            comparison_ids = list(self.comparisons)
            active_comparison_index = project.get("active_comparison_index")
            self.current_comparison_id = (
                comparison_ids[active_comparison_index]
                if isinstance(active_comparison_index, int)
                and 0 <= active_comparison_index < len(comparison_ids)
                else None
            )
            context_index = project.get("calculation_comparison_index")
            context = (
                self.comparisons[comparison_ids[context_index]]
                if isinstance(context_index, int) and 0 <= context_index < len(comparison_ids)
                else self.comparison_record()
            )
            # Older projects did not store the active comparison. Restore the
            # unique shared-integral context if individual spectra have none.
            if (
                context is None
                and "calculation_comparison_index" not in project
                and not any(r.get("integrals") for r in self.spectra.values())
            ):
                candidates = [c for c in self.comparisons.values() if c.get("integrals")]
                if len(candidates) == 1:
                    context = candidates[0]
                    self.current_comparison_id = context["id"]
            if isinstance(workspace, dict):
                self.load_formula_preset(workspace, show_error=False, comparison_context=context)
            self.refresh_spectrum_library()
            self.draw(reset_x=True, reset_y=True)
            view = project.get("view", {})
            if isinstance(view, dict):
                xlim, ylim = view.get("xlim"), view.get("ylim")
                if isinstance(xlim, list) and len(xlim) == 2:
                    self.axes.set_xlim(float(xlim[0]), float(xlim[1]))
                if isinstance(ylim, list) and len(ylim) == 2:
                    self.axes.set_ylim(float(ylim[0]), float(ylim[1]))
                self.canvas.draw_idle()
        except (TypeError, ValueError, KeyError) as error:
            messagebox.showerror(
                "Проект NMR", f"Проект повреждён или несовместим:\n{error}", parent=self
            )
            return
        self.project_path = Path(filename)
        self.status.set(f"Проект открыт: {self.project_path.name}")

    def prepare_project_load(self, project: dict) -> None:
        """Extension hook: Himera can restore embedded raw-processed spectra."""

    def project_source_available(self, source: Path) -> bool:
        return source.is_dir()

    def restore_project_spectrum_payload(self, payload: dict[str, object]) -> None:
        """Применить сохранённую обработку к уже загруженному активному 1r."""
        if "bruker_title" in payload:
            self.spectra[self.active_spectrum_id]["bruker_title"] = clean_title(
                payload["bruker_title"]
            )
        phase = payload.get("phase", {})
        if not isinstance(phase, dict):
            phase = {}
        self.phase0, self.phase1 = float(phase.get("p0", 0.0)), float(phase.get("p1", 0.0))
        reference = phase.get("reference")
        self.phase_reference = (
            int(reference)
            if isinstance(reference, int)
            and self.real is not None
            and 0 <= reference < self.real.size
            else None
        )
        self.ppm_shift = float(payload.get("ppm_shift", 0.0))
        assert self.ppm is not None and self.real is not None
        self.ppm = self.ppm + self.ppm_shift
        self.corrected = self.decode_project_signal(payload.get("corrected_signal"), self.real.size)
        self.restore_project_integrals(
            payload.get("integrals"), payload.get("reference_integral_id")
        )
        self.integration_method.set(
            self.normalize_integration_method(
                str(payload.get("integration_method", "Обычная площадь"))
            )
        )
        display = str(payload.get("integration_display", "Площадь"))
        self.integration_display.set(display if display in self.INTEGRATION_DISPLAYS else "Площадь")
        self.sync_integration_method_choice()

    def restore_project_integrals(self, raw_integrals: object, reference_id: object) -> None:
        self.integrals = []
        if not isinstance(raw_integrals, list):
            return
        for raw in raw_integrals:
            if not isinstance(raw, dict):
                continue
            try:
                integral_id = int(raw["id"])
                left, right = float(raw["left"]), float(raw["right"])
            except (KeyError, TypeError, ValueError):
                continue
            if integral_id <= 0 or not np.isfinite(left) or not np.isfinite(right) or left == right:
                continue
            color = str(
                raw.get("color")
                or self.integral_colors[(integral_id - 1) % len(self.integral_colors)]
            )
            self.integrals.append(
                {
                    "id": integral_id,
                    "left": min(left, right),
                    "right": max(left, right),
                    "color": color,
                    "name": str(raw.get("name") or f"Интеграл {integral_id}"),
                    "number": raw.get("number"),
                }
            )
        # Старые проекты не содержали номер: восстанавливаем привычный порядок
        # слева направо и одновременно устраняем повторы в повреждённых файлах.
        used_numbers: set[int] = set()
        for fallback, item in enumerate(
            sorted(self.integrals, key=lambda value: -float(value["left"]) - float(value["right"])),
            start=1,
        ):
            raw_number = item.get("number")
            number = int(raw_number) if isinstance(raw_number, int) and raw_number > 0 else fallback
            while number in used_numbers:
                number += 1
            item["number"] = number
            used_numbers.add(number)
        ids = {int(item["id"]) for item in self.integrals}
        self.reference_integral_id = (
            int(reference_id) if isinstance(reference_id, int) and reference_id in ids else None
        )
        self._next_integral_id = max(ids, default=0) + 1

    def phased_signal(self) -> np.ndarray:
        assert self.real is not None and self.imaginary is not None
        return phase_correct(self.real, self.imaginary, self.phase0, self.phase1, self.phase_pivot)

    def signal(self) -> np.ndarray:
        return current_signal(self.spectra.current)

    @staticmethod
    def record_signal(record: dict[str, object]) -> np.ndarray:
        """Сигнал записи библиотеки без переключения активного спектра."""
        return current_signal(record)

    def comparison_record(self) -> dict[str, object] | None:
        if self.current_comparison_id is None:
            return None
        return self.comparisons.get(self.current_comparison_id)

    @staticmethod
    def lorentzian_fit_in_region(
        ppm: np.ndarray, signal: np.ndarray, left: float, right: float, *, x_offset: float = 0.0
    ) -> dict[str, float]:
        """Подогнать один лоренциан внутри границ интеграла.

        В обзорном графике границы живут уже в сдвинутой системе координат,
        поэтому перед выборкой их нужно вернуть на ось отдельного спектра.
        """
        low, high = min(left - x_offset, right - x_offset), max(left - x_offset, right - x_offset)
        mask = (ppm >= low) & (ppm <= high)
        if np.count_nonzero(mask) < 7:
            raise ValueError("Для аппроксимации Лоренца выделите не меньше 7 точек спектра.")
        return fit_lorentzian_peak(ppm[mask], signal[mask])

    def lorentzian_integral_area(
        self,
        ppm: np.ndarray,
        signal: np.ndarray,
        left: float,
        right: float,
        *,
        x_offset: float = 0.0,
    ) -> float:
        """Вернуть модуль полной площади fitted-лоренциана.

        При неудачной подгонке таблица остаётся рабочей и показывает ноль;
        при добавлении нового интеграла ошибка сообщается отдельно до записи
        интеграла в проект.
        """
        try:
            fit = self.lorentzian_fit_in_region(ppm, signal, left, right, x_offset=x_offset)
        except (ValueError, RuntimeError, FloatingPointError):
            return 0.0
        return abs(float(fit["area"]))

    def record_integral_area(
        self,
        record: dict[str, object],
        left: float,
        right: float,
        *,
        x_offset: float = 0.0,
        integral: dict | None = None,
    ) -> float:
        """Физическая площадь записи в единицах интенсивность × ppm.

        Формулы всегда используют именно это значение; способ вывода таблицы
        не должен незаметно менять математическую схему пользователя.
        """
        ppm = np.asarray(record["ppm"])
        signal = self.record_signal(record)
        method = self.integration_method.get()
        if method == self.DECONVOLUTION_METHOD:
            return self.lorentzian_integral_area(ppm, signal, left, right, x_offset=x_offset)
        return integrate_region(
            ppm,
            signal,
            left,
            right,
            x_offset=x_offset,
            absolute=method == "Сумма модулей относительно 0",
        )

    def record_integral_display_value(
        self,
        record: dict[str, object],
        left: float,
        right: float,
        *,
        x_offset: float = 0.0,
        integral: dict | None = None,
    ) -> float:
        """Вернуть только отображаемое значение интеграла для таблицы."""
        if self.integration_method.get() == self.DECONVOLUTION_METHOD:
            # Сумма дискретных отсчётов относится только к обычному
            # интегрированию. Для fitted-пика всегда отображаем его полную
            # аналитическую площадь, иначе выбранный способ теряет смысл.
            return self.record_integral_area(
                record, left, right, x_offset=x_offset, integral=integral
            )
        if self.integration_display.get() != "Сумма дискретных точек (MestReNova)":
            return self.record_integral_area(
                record, left, right, x_offset=x_offset, integral=integral
            )
        return integrate_region(
            record["ppm"],
            self.record_signal(record),
            left,
            right,
            x_offset=x_offset,
            discrete=True,
            absolute=self.integration_method.get() == "Сумма модулей относительно 0",
        )

    def visible_slice(self) -> slice:
        assert self.ppm is not None
        left, right = sorted(self.axes.get_xlim())
        indices = np.flatnonzero((self.ppm >= left) & (self.ppm <= right))
        if indices.size < 3:
            return slice(0, self.ppm.size)
        return slice(int(indices[0]), int(indices[-1]) + 1)

    def draw(
        self,
        baseline: np.ndarray | None = None,
        segment: slice | None = None,
        preview: bool = False,
        reset_x: bool = False,
        reset_y: bool = False,
    ) -> None:
        if self.ppm is None:
            return
        if self.current_comparison_id is not None and not preview:
            self.draw_comparison(reset_x=reset_x, reset_y=reset_y)
            return
        self.save_active_spectrum()
        # После обзорного графика в Figure может быть несколько осей. Для
        # обычного спектра снова оставляем ровно одну, привычную всем диалогам.
        if self._comparison_axes:
            self.figure.clear()
            self.axes = self.figure.add_subplot(111)
            self._comparison_axes = []
        old_xlim = self.axes.get_xlim()
        old_ylim = self.axes.get_ylim()
        self.axes.clear()
        self.axes.plot(
            self.ppm,
            self.signal(),
            color="#1f77b4",
            linewidth=0.75,
            label="Спектр" if not preview else "Исходный спектр",
        )
        if self.phase_reference is not None:
            self.axes.axvline(self.ppm[self.phase_reference], color="#d62728", linewidth=1.2)
            self.axes.text(
                self.ppm[self.phase_reference],
                0.97,
                "Опорный пик φ₀",
                color="#d62728",
                transform=self.axes.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=9,
            )
        if baseline is not None and segment is not None:
            self.axes.plot(
                self.ppm[segment],
                baseline,
                "r--",
                linewidth=1.1,
                label="Базовая линия для вычитания",
            )
        self.axes.set_xlabel("Химический сдвиг, ppm")
        self.axes.set_ylabel("Интенсивность, отн. ед.")
        self.axes.set_title(self._title if not preview else "Предпросмотр базовой линии")
        self.axes.grid(alpha=0.25)
        self.draw_integrals()
        if baseline is not None:
            self.axes.legend(loc="best")
        if reset_x:
            self.axes.set_xlim(float(self.ppm.max()), float(self.ppm.min()))
        elif old_xlim != (0.0, 1.0):
            self.axes.set_xlim(old_xlim)
        if not reset_y and old_ylim != (0.0, 1.0):
            self.axes.set_ylim(old_ylim)
        self.create_span_selector()
        self.canvas.draw_idle()
        self.save_active_spectrum()
        if not preview:
            self.refresh_spectrum_library()

    def draw_comparison(self, *, reset_x: bool = False, reset_y: bool = False) -> None:
        """Отрисовать наложение либо стек отдельных спектров на одной оси X."""
        comparison = self.comparison_record()
        if comparison is None:
            return
        ids = [
            int(value) for value in comparison.get("spectrum_ids", []) if int(value) in self.spectra
        ]
        if not ids:
            self.current_comparison_id = None
            self.draw(reset_x=True, reset_y=True)
            return
        old_xlim = self.axes.get_xlim() if hasattr(self, "axes") else (0.0, 1.0)
        old_ylim = self.axes.get_ylim() if hasattr(self, "axes") else (0.0, 1.0)
        self.figure.clear()
        mode = str(comparison.get("mode", "common"))
        # «У каждого своя ось» здесь означает не набор маленьких subplot, а
        # вертикальный стек на одном поле — как в Mnova. Каждый ряд получает
        # собственный визуальный масштаб, исходные данные для интегрирования
        # остаются без нормировки.
        self.axes = self.figure.add_subplot(111)
        self._comparison_axes = [self.axes]
        axis = self.axes
        offsets = comparison.setdefault("offsets", {})
        colors = ("#1f77b4", "#e45756", "#59a14f", "#f28e2b", "#9467bd", "#17becf")
        maximum, minimum = -np.inf, np.inf
        stack_levels: list[float] = []
        for index, spectrum_id in enumerate(ids):
            record = self.spectra[spectrum_id]
            ppm = np.asarray(record["ppm"])
            offset = float(offsets.get(spectrum_id, offsets.get(str(spectrum_id), 0.0)))
            signal = self.record_signal(record)
            color = colors[index % len(colors)]
            if mode == "separate":
                # Медиана устойчива к узким линиям и задаёт положение местной
                # базовой линии. Положительная и отрицательная части имеют
                # разные пределы, чтобы обычный спектр выглядел естественно,
                # а двуполярный не налезал на соседний ряд.
                baseline = float(np.median(signal))
                positive = max(float(np.max(signal) - baseline), np.finfo(float).eps)
                negative = max(float(baseline - np.min(signal)), np.finfo(float).eps)
                scale = min(0.78 / positive, 0.48 / negative)
                level = 1.0 + (len(ids) - 1 - index) * 1.42
                stack_levels.append(level)
                displayed = (signal - baseline) * scale + level
                axis.plot(ppm + offset, displayed, color=color, linewidth=0.75)
                axis.axhline(level, color=color, linewidth=0.65, alpha=0.7)
                axis.text(
                    0.002,
                    level + 0.88,
                    str(record["name"]),
                    color=color,
                    transform=axis.get_yaxis_transform(),
                    ha="left",
                    va="bottom",
                    fontsize=9,
                )
            else:
                axis.plot(
                    ppm + offset, signal, color=color, linewidth=0.75, label=str(record["name"])
                )
            maximum = max(maximum, float(np.max(ppm + offset)))
            minimum = min(minimum, float(np.min(ppm + offset)))
        axis.grid(alpha=0.25)
        axis.set_xlabel("Химический сдвиг, ppm")
        if mode == "common":
            axis.set_ylabel("Интенсивность, отн. ед.")
            axis.set_title(str(comparison["name"]))
            axis.legend(loc="best")
        else:
            axis.set_title(str(comparison["name"]) + " — отдельные шкалы")
            axis.set_ylabel("")
            axis.set_yticks(stack_levels)
            axis.set_yticklabels([str(number) for number in range(len(ids), 0, -1)])
            axis.yaxis.tick_right()
            axis.tick_params(axis="y", length=0, pad=5)
            axis.set_ylim(0.35, max(stack_levels) + 1.06)
        if reset_x or old_xlim == (0.0, 1.0):
            axis.set_xlim(maximum, minimum)
        else:
            axis.set_xlim(old_xlim)
        if not reset_y and old_ylim != (0.0, 1.0):
            axis.set_ylim(old_ylim)
        self.draw_comparison_integrals(axis, comparison)
        self.figure.tight_layout()
        self.create_span_selector()
        self.update_comparison_integral_table(comparison)
        self.canvas.draw_idle()

    def draw_comparison_integrals(self, axis, comparison: dict[str, object]) -> None:
        integrals = list(comparison.get("integrals", []))
        ymin, ymax = axis.get_ylim()
        for item in sorted(integrals, key=lambda value: int(value.get("number", 0))):
            left, right, color = float(item["left"]), float(item["right"]), str(item["color"])
            axis.axvspan(left, right, color=color, alpha=0.11)
            axis.axvline(left, color=color, linewidth=1.1)
            axis.axvline(right, color=color, linewidth=1.1)
            axis.text(
                (left + right) / 2,
                ymax - (ymax - ymin) * 0.035,
                str(item["number"]),
                color=color,
                ha="center",
                va="top",
                fontsize=10,
                fontweight="bold",
            )

    def open_comparison_dialog(self) -> None:
        if len(self.spectra) < 2:
            messagebox.showinfo(
                "Несколько спектров", "Добавьте как минимум два спектра.", parent=self
            )
            return
        MultiSpectrumDialog(self)

    def create_comparison(self, spectrum_ids: list[int], mode: str) -> None:
        ids = [spectrum_id for spectrum_id in spectrum_ids if spectrum_id in self.spectra]
        if len(ids) < 2:
            messagebox.showinfo("Несколько спектров", "Выберите хотя бы два спектра.", parent=self)
            return
        self.integral_boundary_editor.set_active(False)
        self.save_active_spectrum()
        comparison_id = self._next_comparison_id
        self._next_comparison_id += 1
        self.comparisons[comparison_id] = {
            "id": comparison_id,
            "name": f"Сравнение {comparison_id}",
            "spectrum_ids": ids,
            "mode": mode,
            "offsets": {spectrum_id: 0.0 for spectrum_id in ids},
            "integrals": [],
            "next_integral_id": 1,
        }
        self.current_comparison_id = comparison_id
        # Палитра формул показывает площадь первого выбранного спектра, а
        # таблица формул ниже всё равно пересчитывает все строки обзора.
        self.restore_active_spectrum(ids[0])
        self.integration_mode = False
        self.integration_button.configure(relief="raised")
        self.hide_integral_table()
        self.refresh_spectrum_library()
        self.draw_comparison(reset_x=True, reset_y=True)
        text = "общей оси" if mode == "common" else "отдельных осей"
        self.status.set(f"Создано {self.comparisons[comparison_id]['name']} в режиме {text}.")

    def shift_comparison_spectrum(self) -> None:
        self.integral_boundary_editor.set_active(False)
        comparison = self.comparison_record()
        if comparison is None:
            messagebox.showinfo(
                "Сдвиг по X", "Сначала откройте график «Сравнение» справа.", parent=self
            )
            return
        ShiftSpectrumDialog(self, comparison)

    def auto_align_comparison(self) -> bool:
        """Совместить все линии обзора с первым спектром по текущему окну X.

        На полном спектре метод использует все общие пики. Если нужна привязка
        к одному конкретному сигналу, сначала увеличьте его колёсиком и затем
        запустите эту команду: границы текущего окна сохраняются.
        """
        self.integral_boundary_editor.set_active(False)
        comparison = self.comparison_record()
        if comparison is None:
            messagebox.showinfo(
                "Автовыравнивание по X",
                "Сначала откройте график «Сравнение» слева.",
                parent=self,
            )
            return False
        spectrum_ids = [
            int(value) for value in comparison.get("spectrum_ids", []) if int(value) in self.spectra
        ]
        if len(spectrum_ids) < 2:
            messagebox.showinfo(
                "Автовыравнивание по X",
                "Для выравнивания нужны как минимум два спектра.",
                parent=self,
            )
            return False

        reference_id = spectrum_ids[0]
        reference = self.spectra[reference_id]
        offsets = comparison.setdefault("offsets", {})
        reference_offset = float(offsets.get(reference_id, offsets.get(str(reference_id), 0.0)))
        shown_left, shown_right = sorted(self.axes.get_xlim())
        # Окно на графике выражено уже в отображаемой системе p + offset.
        # Алгоритм получает исходную ось опорного спектра.
        raw_left, raw_right = shown_left - reference_offset, shown_right - reference_offset
        proposed = {reference_id: reference_offset}
        aligned: list[tuple[str, float, float]] = []
        failures: list[str] = []

        for spectrum_id in spectrum_ids[1:]:
            record = self.spectra[spectrum_id]
            try:
                result = find_x_shift(
                    np.asarray(reference["ppm"]),
                    self.record_signal(reference),
                    np.asarray(record["ppm"]),
                    self.record_signal(record),
                    raw_left,
                    raw_right,
                )
            except AlignmentError as error:
                failures.append(f"{record['name']}: {error}")
                continue
            proposed[spectrum_id] = reference_offset + result.shift
            aligned.append((str(record["name"]), result.shift, result.correlation))

        if not aligned:
            messagebox.showwarning(
                "Автовыравнивание по X",
                "Ни один спектр не удалось совместить с опорным.\n\n" + "\n".join(failures[:4]),
                parent=self,
            )
            return False

        changed = any(
            not np.isclose(
                float(offsets.get(spectrum_id, offsets.get(str(spectrum_id), 0.0))),
                offset,
                atol=5e-7,
            )
            for spectrum_id, offset in proposed.items()
        )
        if changed:
            self.remember_comparison_offsets(comparison, "автовыравнивание спектров по X")
            offsets.update(proposed)
            self.draw_comparison()

        average = float(np.mean([score for _name, _shift, score in aligned]))
        self.status.set(
            f"Автовыравнивание: совмещено {len(aligned)} из {len(spectrum_ids) - 1}; "
            f"средняя корреляция {average:.3f}. Ctrl+Z отменит сдвиги."
        )
        if failures:
            messagebox.showwarning(
                "Автовыравнивание по X",
                "Часть спектров не удалось совместить автоматически:\n\n"
                + "\n".join(failures[:6])
                + "\n\nОстальные линии уже выровнены; проблемные можно довести вручную.",
                parent=self,
            )
        return changed

    def create_span_selector(self) -> None:
        """Создать выделение заново: ``axes.clear()`` удаляет его художники."""
        if self.span is not None:
            self.span.disconnect_events()
        self.span = SpanSelector(
            self.axes,
            self.on_x_selected,
            "horizontal",
            useblit=True,
            props={"facecolor": "#4c78a8", "alpha": 0.22},
        )
        self.span.set_active(self.integration_mode or self.x_zoom_mode)
        editor = self.__dict__.get("integral_boundary_editor")
        if editor is not None:
            editor.refresh_handles()

    def restore_interaction_mode(self) -> None:
        """Вернуть выделение после закрытия модального окна или смены метода."""
        if self.span is not None:
            editor = self.__dict__.get("integral_boundary_editor")
            self.span.set_active(
                (self.integration_mode or self.x_zoom_mode) and not (editor and editor.active)
            )

    def zoom_y(self, event) -> None:
        active_axes = (
            self._comparison_axes if self.current_comparison_id is not None else [self.axes]
        )
        if event.inaxes not in active_axes:
            return
        axis = event.inaxes
        bottom, top = axis.get_ylim()
        factor = 0.8 if event.button == "up" else 1.25
        new_range = (top - bottom) * factor
        # Ноль — фиксированная опора масштаба. Для спектра с пиками обоих
        # знаков он стоит по центру; для обычного NMR — в нижней десятой
        # части экрана, оставляя место для положительных пиков.
        if self.current_comparison_id is not None:
            comparison = self.comparison_record()
            assert comparison is not None
            if str(comparison.get("mode")) == "separate":
                # В вертикальном стеке нет единого физического нуля: уровни
                # рядов являются только визуальным размещением. Масштабируем
                # весь стек вокруг его центра, не склеивая линии обратно.
                centre = (bottom + top) / 2
                axis.set_ylim(centre - new_range / 2, centre + new_range / 2)
                self.canvas.draw_idle()
                return
            # В наложении сохраняем прежнюю логику, привязанную к базовой
            # линии первого спектра, а не к положению курсора.
            ids = [int(value) for value in comparison["spectrum_ids"]]
            visible = self.record_signal(self.spectra[ids[0]])
        else:
            visible = self.signal()[self.visible_slice()]
        if peak_polarity(visible) == 0:
            axis.set_ylim(-new_range / 2, new_range / 2)
        else:
            axis.set_ylim(-new_range / 10, new_range * 9 / 10)
        self.canvas.draw_idle()

    def toggle_x_zoom(self) -> None:
        self.integral_boundary_editor.set_active(False)
        if self.integration_mode:
            self.integration_mode = False
            self.integration_button.configure(relief="raised")
            self.hide_integral_table()
            self.x_zoom_mode = True
        else:
            self.x_zoom_mode = not self.x_zoom_mode
        assert self.span is not None
        self.span.set_active(self.x_zoom_mode)
        self.zoom_button.configure(relief="sunken" if self.x_zoom_mode else "raised")
        self.status.set(
            "Протяните мышью нужный диапазон ppm."
            if self.x_zoom_mode
            else "Выбор диапазона X выключен."
        )

    def on_x_selected(self, xmin: float, xmax: float) -> None:
        if self.integral_boundary_editor.active:
            return
        if abs(xmax - xmin) < 1e-9:
            return
        if self.integration_mode:
            if self.current_comparison_id is not None:
                self.add_comparison_integral(xmin, xmax)
            else:
                self.add_integral(xmin, xmax)
            return
        self.axes.set_xlim(max(xmin, xmax), min(xmin, xmax))
        if self.auto_y_on_switch.get():
            self.fit_current_view_y()
        self.x_zoom_mode = False
        assert self.span is not None
        self.span.set_active(False)
        self.zoom_button.configure(relief="raised")
        self.canvas.draw_idle()
        self.status.set("Выбранный диапазон будет использован для коррекции базовой линии.")

    def reset_x_zoom(self) -> None:
        if self.current_comparison_id is not None:
            self.draw_comparison(reset_x=True)
        elif self.ppm is not None:
            self.axes.set_xlim(float(self.ppm.max()), float(self.ppm.min()))
            self.canvas.draw_idle()
        if self.auto_y_on_switch.get():
            self.fit_current_view_y()

    def show_integral_table(self) -> None:
        """Показать таблицу вторым окном вертикального разделителя."""
        if not self._integral_table_visible:
            self.spectrum_split.add(self.table_frame, minsize=120)
            self._integral_table_visible = True
            self.after_idle(self.apply_graph_height)
        self.integral_table_visible.set(True)

    def hide_integral_table(self) -> None:
        if self._integral_table_visible:
            self.capture_view_layout()
            self.spectrum_split.forget(self.table_frame)
            self._integral_table_visible = False
        self.integral_table_visible.set(False)

    def apply_graph_height(self) -> None:
        """Установить положение разделителя в процентах от доступной высоты."""
        if not self._integral_table_visible:
            return
        self.update_idletasks()
        total_height = self.spectrum_split.winfo_height()
        if total_height < 250:
            return
        # Оставляем таблице как минимум 120 px даже при слишком маленьком окне.
        graph_height = min(int(total_height * self.graph_height_percent / 100), total_height - 120)
        graph_height = max(180, graph_height)
        try:
            self.spectrum_split.sash_place(0, 0, graph_height)
        except tk.TclError:
            # До первого размещения панелей sash ещё может не существовать.
            self.after(40, self.apply_graph_height)

    def configure_graph_size(self) -> None:
        """Точная настройка высоты графика; разделитель также можно тянуть."""
        if not self.integration_mode:
            messagebox.showinfo(
                "Размер графика",
                "Включите режим интегрирования: появится таблица и разделитель высоты.",
                parent=self,
            )
            return
        percent = simpledialog.askinteger(
            "Размер графика",
            "Высота графика, % от доступного пространства (30–85):\n"
            "Разделитель между графиком и таблицей также можно перетянуть мышью.",
            initialvalue=self.graph_height_percent,
            minvalue=30,
            maxvalue=85,
            parent=self,
        )
        if percent is not None:
            self.graph_height_percent = percent
            self.apply_graph_height()

    def toggle_integration(self) -> None:
        if self.real is None and self.current_comparison_id is None:
            messagebox.showinfo("Нет спектра", "Сначала загрузите спектр.", parent=self)
            return
        self.integral_boundary_editor.set_active(False)
        self.integration_mode = not self.integration_mode
        self.x_zoom_mode = False
        self.zoom_button.configure(relief="raised")
        assert self.span is not None
        self.span.set_active(self.integration_mode)
        self.zoom_button.configure(relief="raised")
        self.integration_button.configure(relief="sunken" if self.integration_mode else "raised")
        if self.integration_mode:
            self.show_integral_table()
            if self.current_comparison_id is not None:
                comparison = self.comparison_record()
                if comparison is not None:
                    self.update_comparison_integral_table(comparison)
            else:
                self.update_integral_table(self.ordered_integrals())
        else:
            self.hide_integral_table()
        self.status.set(
            "Режим интегрирования: протяните мышью границы пика."
            if self.integration_mode
            else "Режим интегрирования выключен."
        )

    def toggle_integral_boundaries(self) -> None:
        """Enable direct manipulation without also creating new integrals."""
        if self.real is None and self.current_comparison_id is None:
            messagebox.showinfo("Нет спектра", "Сначала загрузите спектр.", parent=self)
            return
        if self._spectrum_dialog_open:
            return
        self.integral_boundary_editor.toggle()

    def add_integral(self, xmin: float, xmax: float) -> None:
        left, right = min(xmin, xmax), max(xmin, xmax)
        if self.ppm is None or np.count_nonzero((self.ppm >= left) & (self.ppm <= right)) < 3:
            return
        if self.integration_method.get() == self.DECONVOLUTION_METHOD:
            try:
                self.lorentzian_fit_in_region(self.ppm, self.signal(), left, right)
            except (ValueError, RuntimeError, FloatingPointError) as error:
                messagebox.showwarning("Деконволюция", str(error), parent=self)
                return
        previous = self.capture_spectrum_state()
        integral_id = self._next_integral_id
        self._next_integral_id += 1
        self.integrals.append(
            {
                "id": integral_id,
                "left": left,
                "right": right,
                "color": self.integral_colors[(integral_id - 1) % len(self.integral_colors)],
                "name": f"Интеграл {integral_id}",
                "number": max((int(item.get("number", 0)) for item in self.integrals), default=0)
                + 1,
            }
        )
        self.remember_spectrum_state(previous, "добавление интеграла")
        self.draw()
        if self.integration_method.get() == self.DECONVOLUTION_METHOD:
            self.status.set(
                "Интеграл добавлен: пунктиром показан лоренциан, в таблице — его полная площадь."
            )
        else:
            self.status.set("Интеграл добавлен. Правый клик по нему открывает действия.")

    def add_comparison_integral(self, xmin: float, xmax: float) -> None:
        """Один выделенный диапазон применяется ко всем линиям обзора."""
        comparison = self.comparison_record()
        if comparison is None:
            return
        left, right = min(xmin, xmax), max(xmin, xmax)
        if self.integration_method.get() == self.DECONVOLUTION_METHOD:
            offsets = comparison.get("offsets", {})
            failed: list[str] = []
            for spectrum_id in [int(value) for value in comparison.get("spectrum_ids", [])]:
                record = self.spectra.get(spectrum_id)
                if record is None:
                    continue
                offset = float(offsets.get(spectrum_id, offsets.get(str(spectrum_id), 0.0)))
                try:
                    self.lorentzian_fit_in_region(
                        np.asarray(record["ppm"]),
                        self.record_signal(record),
                        left,
                        right,
                        x_offset=offset,
                    )
                except (ValueError, RuntimeError, FloatingPointError):
                    failed.append(str(record["name"]))
            if failed:
                messagebox.showwarning(
                    "Деконволюция",
                    "Для некоторых спектров в выбранном диапазоне нельзя надёжно построить лоренциан:\n"
                    + ", ".join(failed),
                    parent=self,
                )
                return
        self.remember_comparison_integrals(comparison, "добавление общего интеграла")
        integral_id = int(comparison.get("next_integral_id", 1))
        comparison["next_integral_id"] = integral_id + 1
        integrals = comparison.setdefault("integrals", [])
        next_number = max((int(item.get("number", 0)) for item in integrals), default=0) + 1
        integrals.append(
            {
                "id": integral_id,
                "number": next_number,
                "left": left,
                "right": right,
                "color": self.integral_colors[(integral_id - 1) % len(self.integral_colors)],
            }
        )
        self.draw_comparison()
        if self.integration_method.get() == self.DECONVOLUTION_METHOD:
            self.status.set(
                "Общий интеграл добавлен: для каждого спектра рассчитана полная площадь его лоренциана."
            )
        else:
            self.status.set(
                "Общий интеграл добавлен: площади рассчитаны отдельно для каждого спектра."
            )

    @staticmethod
    def comparison_integral_at(
        comparison: dict[str, object], ppm: float
    ) -> dict[str, object] | None:
        """Вернуть верхний общий интеграл, в чьи пределы попала мышь."""
        return next(
            (
                item
                for item in reversed(list(comparison.get("integrals", [])))
                if float(item["left"]) <= ppm <= float(item["right"])
            ),
            None,
        )

    def remove_comparison_integral(self, item: dict[str, object]) -> None:
        comparison = self.comparison_record()
        if comparison is None:
            return
        integrals = comparison.get("integrals", [])
        if item not in integrals:
            return
        number = int(item.get("number", 0))
        self.remember_comparison_integrals(comparison, f"удаление общего интеграла I{number}")
        integrals.remove(item)
        self.draw_comparison()
        self.status.set(f"Общий интеграл I{number} удалён. Ctrl+Z вернёт его.")

    def ordered_integrals(self) -> list[dict[str, float | int | str]]:
        """Интегралы в заданном пользователем порядке номеров."""
        return self.ordered_integral_items(self.integrals)

    @staticmethod
    def ordered_integral_items(items):
        return sorted(
            items,
            key=lambda item: (
                int(item.get("number", 10**9)),
                -float(item["left"]) - float(item["right"]),
            ),
        )

    def integral_area(self, item: dict[str, float | int | str]) -> float:
        assert self.ppm is not None
        if self.active_spectrum_id in self.spectra:
            return self.record_integral_area(
                self.spectra[self.active_spectrum_id],
                float(item["left"]),
                float(item["right"]),
                integral=item,
            )
        signal = self.signal()
        method = self.integration_method.get()
        if method == self.DECONVOLUTION_METHOD:
            return self.lorentzian_integral_area(
                self.ppm, signal, float(item["left"]), float(item["right"])
            )
        return integrate_region(
            self.ppm,
            signal,
            item["left"],
            item["right"],
            absolute=method == "Сумма модулей относительно 0",
        )

    def integral_display_value(self, item: dict[str, float | int | str]) -> float:
        """Число для таблицы: физическая площадь либо сумма её отсчётов."""
        if self.integration_method.get() == self.DECONVOLUTION_METHOD:
            return self.integral_area(item)
        if self.integration_display.get() != "Сумма дискретных точек (MestReNova)":
            return self.integral_area(item)
        assert self.ppm is not None
        if self.active_spectrum_id in self.spectra:
            return self.record_integral_display_value(
                self.spectra[self.active_spectrum_id],
                float(item["left"]),
                float(item["right"]),
                integral=item,
            )
        return integrate_region(
            self.ppm,
            self.signal(),
            item["left"],
            item["right"],
            discrete=True,
            absolute=self.integration_method.get() == "Сумма модулей относительно 0",
        )

    def sync_integration_method_choice(self) -> None:
        """Синхронизировать combobox и радиокнопки с активным спектром."""
        self.integration_choice.set(self.integration_method.get())
        self.integration_display_choice.set(self.integration_display.get())

    def change_integration_method(self, method: str) -> None:
        """Сменить метод интегрирования как обычное отменяемое действие."""
        method = self.normalize_integration_method(method)
        previous = self.capture_spectrum_state()
        self.integration_method.set(method)
        self.sync_integration_method_choice()
        self.remember_spectrum_state(previous, "смена способа расчёта интегралов")
        self.refresh_integrals()

    def change_integration_display(self, display: str) -> None:
        """Изменить только единицы вывода таблицы, не затрагивая формулы."""
        if display not in self.INTEGRATION_DISPLAYS:
            display = "Площадь"
        previous = self.capture_spectrum_state()
        self.integration_display.set(display)
        self.sync_integration_method_choice()
        self.remember_spectrum_state(previous, "смена способа вывода интегралов")
        self.refresh_integrals()

    def refresh_integrals(self) -> None:
        """Пересчитать таблицу после смены способа интегрирования."""
        if self.integration_method.get() == self.DECONVOLUTION_METHOD:
            # При этом способе пунктирная fitted-кривая — часть результата,
            # поэтому требуется полноценная перерисовка, а не только таблица.
            self.draw()
            return
        comparison = self.comparison_record()
        if comparison is not None:
            self.update_comparison_integral_table(comparison)
        else:
            self.update_integral_table(self.ordered_integrals())
        self.canvas.draw_idle()

    def draw_integrals(self, items=None) -> None:
        if self.ppm is None:
            return
        ordered = self.ordered_integrals() if items is None else self.ordered_integral_items(items)
        ymin, ymax = self.axes.get_ylim()
        for item in ordered:
            number = self.integral_number(item["id"])
            left, right = float(item["left"]), float(item["right"])
            color = str(item["color"])
            self.axes.axvspan(left, right, color=color, alpha=0.12)
            self.axes.axvline(left, color=color, linewidth=1.2)
            self.axes.axvline(right, color=color, linewidth=1.2)
            self.axes.text(
                (left + right) / 2,
                ymax - (ymax - ymin) * 0.035,
                number,
                color=color,
                ha="center",
                va="top",
                fontsize=11,
                fontweight="bold",
            )
            if self.integration_method.get() == self.DECONVOLUTION_METHOD:
                try:
                    fit = self.lorentzian_fit_in_region(self.ppm, self.signal(), left, right)
                except (ValueError, RuntimeError, FloatingPointError):
                    continue
                half_width = float(fit["half_width"])
                center = float(fit["center"])
                extent = max(4.0 * half_width, (right - left) / 2.0)
                curve_x = np.linspace(
                    max(float(self.ppm.min()), center - extent),
                    min(float(self.ppm.max()), center + extent),
                    240,
                )
                curve_y = (
                    lorentzian_component(curve_x, fit["amplitude"], center, half_width)
                    + fit["background_offset"]
                    + fit["background_slope"] * (curve_x - fit["background_center"])
                )
                self.axes.plot(
                    curve_x, curve_y, color=color, linestyle="--", linewidth=1.25, alpha=0.95
                )
        self.update_integral_table(ordered)

    def update_integral_table(self, ordered: list[dict[str, float | int | str]]) -> None:
        table_view = self._table_view_before_refresh()
        self.configure_normal_integral_table()
        for row in self.integral_table.get_children():
            self.integral_table.delete(row)
        reference_area = next(
            (
                self.integral_display_value(item)
                for item in self.integrals
                if int(item["id"]) == self.reference_integral_id
            ),
            None,
        )
        for item in ordered:
            number = self.integral_number(item["id"])
            area = self.integral_display_value(item)
            relative = "—" if not reference_area else f"{area / reference_area:.5g}"
            name = str(item["name"]) + (
                " (1.000)" if int(item["id"]) == self.reference_integral_id else ""
            )
            limits = f"{float(item['left']):.4f} … {float(item['right']):.4f}"
            self.integral_table.insert(
                "",
                "end",
                iid=str(item["id"]),
                values=(number, name, limits, f"{area:.6g}", relative),
            )
        self._table_view_after_refresh(table_view)
        if hasattr(self, "formula_canvas"):
            self.refresh_formula_workspace()

    def configure_normal_integral_table(self) -> None:
        columns = ("number", "name", "limits", "area", "relative")
        reset_columns = tuple(self.integral_table.cget("columns")) != columns
        if self.integration_method.get() == self.DECONVOLUTION_METHOD:
            area_heading = "Площадь Лоренца"
        else:
            area_heading = (
                "Сумма точек"
                if self.integration_display.get() == "Сумма дискретных точек (MestReNova)"
                else "Площадь"
            )
        headings = {
            "number": "№",
            "name": "Название",
            "limits": "Пределы, ppm",
            "area": area_heading,
            "relative": "Отн. площадь",
        }
        widths = {"number": 45, "name": 140, "limits": 180, "area": 160, "relative": 130}
        if reset_columns:
            self.integral_table.configure(columns=columns, show="headings")
        for column in columns:
            self.integral_table.heading(column, text=headings[column])
            if reset_columns:
                self.integral_table.column(
                    column, width=widths[column], anchor="center", stretch=True
                )

    def update_comparison_integral_table(self, comparison: dict[str, object]) -> None:
        """Таблица обзора: одна строка на спектр, одна колонка на диапазон."""
        table_view = self._table_view_before_refresh()
        integrals = sorted(
            list(comparison.get("integrals", [])), key=lambda item: int(item["number"])
        )
        columns = ("spectrum",) + tuple(f"i_{int(item['id'])}" for item in integrals)
        reset_columns = tuple(self.integral_table.cget("columns")) != columns
        if reset_columns:
            self.integral_table.configure(columns=columns, show="headings")
        self.integral_table.heading("spectrum", text="Спектр")
        if reset_columns:
            self.integral_table.column("spectrum", width=155, anchor="w", stretch=True)
        for item in integrals:
            column = f"i_{int(item['id'])}"
            self.integral_table.heading(column, text=f"I{int(item['number'])}")
            if reset_columns:
                self.integral_table.column(column, width=120, anchor="center", stretch=True)
        for row in self.integral_table.get_children():
            self.integral_table.delete(row)
        offsets = comparison.get("offsets", {})
        for spectrum_id in [int(value) for value in comparison.get("spectrum_ids", [])]:
            record = self.spectra.get(spectrum_id)
            if record is None:
                continue
            offset = float(offsets.get(spectrum_id, offsets.get(str(spectrum_id), 0.0)))
            values = [str(record["name"])]
            values.extend(
                f"{self.record_integral_display_value(record, float(item['left']), float(item['right']), x_offset=offset, integral=item):.6g}"
                for item in integrals
            )
            self.integral_table.insert("", "end", iid=f"s{spectrum_id}", values=values)
        self._table_view_after_refresh(table_view)
        if hasattr(self, "formula_canvas"):
            self.refresh_formula_workspace()

    def copy_treeview_rows(self, table: ttk.Treeview, *, context: str) -> bool:
        """Скопировать выделенные строки Treeview либо всю таблицу в TSV для Excel."""
        row_ids = list(table.selection()) or list(table.get_children())
        columns = tuple(str(column) for column in table.cget("columns"))
        if not row_ids or not columns:
            return False
        header = [str(table.heading(column, "text")) for column in columns]
        rows = [
            "\t".join(str(value) for value in table.item(row_id, "values")) for row_id in row_ids
        ]
        self.clipboard_clear()
        self.clipboard_append("\r\n".join(("\t".join(header), *rows)))
        amount = (
            f"выделенных строк: {len(row_ids)}" if table.selection() else f"строк: {len(row_ids)}"
        )
        if context != "integrals":
            self.formula_status.set(f"Таблица результатов скопирована в буфер ({amount}).")
        else:
            self.status.set(f"Таблица интегралов скопирована в буфер ({amount}).")
        return True

    def copy_integral_table(self, _event=None) -> str:
        """Кнопка и Ctrl+C для таблицы обычных либо общих интегралов."""
        if not self.copy_treeview_rows(self.integral_table, context="integrals"):
            self.status.set("В таблице интегралов пока нет данных для копирования.")
        return "break"

    def copy_integral_table_shortcut(self, event) -> str | None:
        keysym = str(getattr(event, "keysym", "")).lower()
        is_c_key = keysym in {"c", "cyrillic_es", "с"} or int(getattr(event, "keycode", -1)) == 67
        return self.copy_integral_table(event) if is_c_key else None

    def integral_table_menu(self, event) -> str:
        """Контекстное меню строки таблицы; номер не связан с внутренним ID."""
        row_id = self.integral_table.identify_row(event.y)
        comparison = self.comparison_record()
        if comparison is not None:
            column = self.integral_table.identify_column(event.x)
            try:
                column_index = int(column.removeprefix("#")) - 1
            except ValueError:
                return "break"
            integrals = sorted(
                list(comparison.get("integrals", [])), key=lambda item: int(item["number"])
            )
            # Первый столбец — название спектра; остальные соответствуют I1,
            # I2… и потому подходят для удаления общей области.
            if not row_id or not 1 <= column_index <= len(integrals):
                return "break"
            item = integrals[column_index - 1]
            self.integral_table.selection_set(row_id)
            menu = tk.Menu(self, tearoff=False)
            menu.add_command(
                label=f"Убрать общий интеграл I{int(item['number'])}",
                command=lambda selected=item: self.remove_comparison_integral(selected),
            )
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"
        if not row_id:
            return "break"
        try:
            item = self.integral_by_id(int(row_id))
        except ValueError:
            item = None
        if item is None:
            return "break"
        self.integral_table.selection_set(row_id)
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Изменить номер…", command=lambda: self.rename_integral_number(item))
        menu.add_command(label="Переименовать…", command=lambda: self.rename_integral(item))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    # ---------- Первый прототип вкладки формул (не используется) ----------

    def on_tab_changed(self, _event: object) -> None:
        if self.notebook.select() == str(self.calculations_tab):
            self.refresh_formula_workspace()

    def integral_by_id(self, integral_id: object) -> dict[str, float | int | str] | None:
        return next((item for item in self.integrals if int(item["id"]) == integral_id), None)

    def integral_number(self, integral_id: object) -> str:
        for fallback, item in enumerate(self.ordered_integrals(), start=1):
            if int(item["id"]) == integral_id:
                number = item.get("number", fallback)
                return str(number)
        return "?"

    @staticmethod
    def format_formula_value(value: float | None) -> str:
        return "—" if value is None or not np.isfinite(value) else f"{value:.6g}"

    # ---------- Preset calculations and the independent advanced editor ----------

    def open_calculations(self):
        self.notebook.select(self.calculations_tab)
        self.refresh_formula_workspace()

    def open_formula_editor(self):
        if self.formula_editor is not None and self.formula_editor.winfo_exists():
            self.formula_editor.deiconify()
            self.formula_editor.lift()
            self.formula_editor.focus_set()
            return self.formula_editor
        from formula_editor import FormulaEditor

        self.formula_editor = FormulaEditor(self)
        return self.formula_editor

    def formula_source_integrals(self):
        comparison = self.calculation_comparison_record()
        if comparison is not None:
            return sorted(comparison.get("integrals", []), key=lambda item: int(item["number"]))
        return self.ordered_integrals()

    def formula_preset_data(self):
        return self.formula_document.to_preset(self.formula_source_integrals())

    def invalidate_formula_results(self):
        self._formula_engines.clear()

    def resolve_formula_area(self, block, record):
        if record is None:
            return None
        number = block.get("area_number")
        if not isinstance(number, int):
            item = next(
                (i for i in record.get("integrals", []) if i["id"] == block.get("integral_id")),
                None,
            )
            number = item.get("number") if item else None
        stored = block.get("comparison_id")
        comparison = (
            self.comparisons.get(stored) if isinstance(stored, int) else self.comparison_record()
        )
        if comparison is not None and record["id"] in comparison.get("spectrum_ids", []):
            item = next(
                (i for i in comparison.get("integrals", []) if i.get("number") == number), None
            )
            if item is not None:
                offsets = comparison.get("offsets", {})
                offset = float(offsets.get(record["id"], offsets.get(str(record["id"]), 0)))
                return self.record_integral_area(
                    record, item["left"], item["right"], x_offset=offset, integral=item
                )
        item = next((i for i in record.get("integrals", []) if i.get("number") == number), None)
        return (
            self.record_integral_area(record, item["left"], item["right"], integral=item)
            if item
            else None
        )

    def formula_engine_for_record(self, record):
        key = record["id"] if record is not None else None
        if key not in self._formula_engines:
            self._formula_engines[key] = FormulaEngine(
                self.formula_document.blocks, lambda block: self.resolve_formula_area(block, record)
            )
        return self._formula_engines[key]

    def calculation_comparison_record(self):
        comparison = self.comparison_record()
        if comparison is None:
            stored = {
                b.get("comparison_id")
                for b in self.formula_document.blocks.values()
                if b.get("kind") == "area" and isinstance(b.get("comparison_id"), int)
            }
            if len(stored) == 1:
                comparison = self.comparisons.get(next(iter(stored)))
        return comparison

    def calculation_records(self):
        comparison = self.calculation_comparison_record()
        if comparison is not None:
            return [
                self.spectra[i] for i in comparison.get("spectrum_ids", []) if i in self.spectra
            ]
        record = self.spectra.get(self.active_spectrum_id)
        return [record] if record is not None else []

    def calculation_rows(self):
        return [
            (record, self.formula_engine_for_record(record).outputs())
            for record in self.calculation_records()
        ]

    def refresh_calculations(self):
        self.invalidate_formula_results()
        if hasattr(self, "calculations_view"):
            self.calculations_view.refresh_results()
            self.calculations_view.refresh_library()

    def refresh_formula_workspace(self):
        self.save_active_spectrum()
        self.refresh_calculations()
        if self.formula_editor is not None and self.formula_editor.winfo_exists():
            self.formula_editor.refresh_formula_workspace()

    def apply_calculation_preset(self, key):
        entry = self.preset_library.entries.get(key)
        if entry is None:
            return False
        preset = dict(entry.preset)
        preset["name"] = entry.name
        return self.load_formula_preset(preset, preset_key=key)

    def load_formula_preset(
        self, preset=None, *, show_error=True, parent=None, preset_key=None, comparison_context=None
    ):
        parent = parent or self
        if preset is None:
            filename = filedialog.askopenfilename(
                parent=parent, title="Открыть формулу", filetypes=(("Формула NOX", "*.json"),)
            )
            if not filename:
                return False
            try:
                entry = self.preset_library.register(filename, import_copy=True)
                preset = dict(entry.preset)
                preset["name"] = entry.name
                preset_key = entry.key
            except (OSError, ValueError, TypeError, KeyError) as error:
                messagebox.showerror("Формула", str(error), parent=parent)
                return False
        try:
            comparison = comparison_context or self.comparison_record()
            available = (
                sorted(comparison.get("integrals", []), key=lambda item: int(item["number"]))
                if comparison is not None
                else self.ordered_integrals()
            )
            document = FormulaDocument.from_preset(
                preset, available, comparison.get("id") if comparison is not None else None
            )
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            if show_error:
                messagebox.showerror(
                    "Формула", f"Не удалось открыть расчёт:\n{error}", parent=parent
                )
            return False
        previous = self.capture_formula_state()
        self.formula_document.restore(document.snapshot())
        self.active_preset_key = preset_key
        if self.formula_editor is not None:
            self.formula_editor.reset_interaction()
        self.refresh_formula_workspace()
        self.formula_status.set(
            f"Выбран расчёт «{document.name}». Недостающие интегралы считаются за 0."
        )
        if show_error:
            self.remember_formula_state(previous, "выбрана формула", preserve_preset=True)
        return True

    def save_formula_preset(self, *, parent=None):
        parent = parent or self
        if not self.formula_document.blocks:
            messagebox.showinfo("Формула", "Сначала создайте формулу в редакторе.", parent=parent)
            return
        directory = self.preset_library.user_directory
        suggested_name = (
            "".join("_" if c in '\\/:*?"<>|' else c for c in self.formula_document.name)
            .strip()
            .rstrip(".")
            or "Расчёт"
        )
        filename = filedialog.asksaveasfilename(
            parent=parent,
            title="Сохранить формулу",
            initialdir=str(directory if directory.exists() else directory.parent),
            initialfile=suggested_name + ".json",
            defaultextension=".json",
            filetypes=(("Формула NOX", "*.json"),),
        )
        if not filename:
            return
        try:
            preset = self.formula_preset_data()
            preset["name"] = Path(filename).stem
            Path(filename).write_text(
                json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            entry = self.preset_library.register(filename, import_copy=True)
        except (OSError, ValueError, TypeError, KeyError) as error:
            messagebox.showerror("Формула", f"Не удалось сохранить:\n{error}", parent=parent)
            return
        self.formula_document.name = entry.name
        self.active_preset_key = entry.key
        self.refresh_calculations()
        self.formula_status.set(f"Формула «{entry.name}» сохранена и добавлена в библиотеку.")

    def on_plot_click(self, event) -> None:
        if self.integral_boundary_editor.dragging:
            return
        if event.button != 3 or event.xdata is None:
            return
        if self.current_comparison_id is not None:
            comparison = self.comparison_record()
            if (
                comparison is None
                or event.inaxes not in self._comparison_axes
                or event.guiEvent is None
            ):
                return
            item = self.comparison_integral_at(comparison, float(event.xdata))
            if item is None:
                return
            number = int(item.get("number", 0))
            menu = tk.Menu(self, tearoff=False)
            menu.add_command(
                label=f"Убрать общий интеграл I{number}",
                command=lambda selected=item: self.remove_comparison_integral(selected),
            )
            try:
                menu.tk_popup(event.guiEvent.x_root, event.guiEvent.y_root)
            finally:
                menu.grab_release()
            return
        if event.inaxes is not self.axes:
            return
        item = next(
            (
                candidate
                for candidate in reversed(self.integrals)
                if float(candidate["left"]) <= event.xdata <= float(candidate["right"])
            ),
            None,
        )
        if event.guiEvent is None:
            return
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(
            label="Задать химический сдвиг…", command=lambda: self.calibrate_shift(event.xdata)
        )
        if item is not None:
            menu.add_separator()
            menu.add_command(
                label="Калибровать как 1.000", command=lambda: self.calibrate_integral(item)
            )
            menu.add_command(
                label="Изменить номер…", command=lambda: self.rename_integral_number(item)
            )
            menu.add_command(label="Переименовать…", command=lambda: self.rename_integral(item))
            menu.add_separator()
            menu.add_command(label="Убрать интеграл", command=lambda: self.remove_integral(item))
        menu.tk_popup(event.guiEvent.x_root, event.guiEvent.y_root)

    def calibrate_shift(self, clicked_ppm: float) -> None:
        """Сдвинуть всю ось так, чтобы выбранный пик получил известное ppm."""
        reference = simpledialog.askfloat(
            "Калибровка химического сдвига",
            f"Текущая координата выбранного пика: {clicked_ppm:.5f} ppm\n"
            "Введите его известный химический сдвиг, ppm:",
            initialvalue=round(clicked_ppm, 5),
            parent=self,
        )
        if reference is None or self.ppm is None:
            return
        previous = self.capture_spectrum_state()
        shift = reference - clicked_ppm
        self.ppm = self.ppm + shift
        self.ppm_shift += shift
        record = self.spectra.get(self.active_spectrum_id, {})
        for step in record.get("baseline_steps", []):
            step["low"] += shift
            step["high"] += shift
        for item in self.integrals:
            item["left"] = float(item["left"]) + shift
            item["right"] = float(item["right"]) + shift
        self.remember_spectrum_state(previous, "калибровка химического сдвига")
        self.draw()
        self.status.set(f"Ось химических сдвигов сдвинута на {shift:+.5f} ppm.")

    def calibrate_integral(self, item: dict[str, float | int | str]) -> None:
        previous = self.capture_spectrum_state()
        self.reference_integral_id = int(item["id"])
        self.remember_spectrum_state(previous, "калибровка интеграла")
        self.draw()
        self.status.set("Выбранный интеграл принят за единицу площади.")

    def rename_integral(self, item: dict[str, float | int | str]) -> None:
        name = simpledialog.askstring(
            "Название интеграла", "Название:", initialvalue=str(item["name"]), parent=self
        )
        if name:
            previous = self.capture_spectrum_state()
            item["name"] = name
            self.remember_spectrum_state(previous, "переименование интеграла")
            self.draw()

    def rename_integral_number(self, item: dict[str, float | int | str]) -> None:
        """Изменить отображаемый номер, сохранив ссылки формул на стабильный ID."""
        current = int(item.get("number", 1))
        number = simpledialog.askinteger(
            "Номер интеграла",
            "Новый номер интеграла:",
            initialvalue=current,
            minvalue=1,
            parent=self,
        )
        if number is None or number == current:
            return
        previous = self.capture_spectrum_state()
        # Номера остаются уникальными: если занятый номер выбран намеренно,
        # меняем два номера местами, а не ломаем порядок палитры и пресетов.
        occupied = next(
            (
                other
                for other in self.integrals
                if other is not item and int(other.get("number", -1)) == number
            ),
            None,
        )
        if occupied is not None:
            occupied["number"] = current
        item["number"] = number
        self.remember_spectrum_state(previous, "изменение номера интеграла")
        self.draw()
        self.status.set(f"Интегралу присвоен номер {number}.")

    def remove_integral(self, item: dict[str, float | int | str]) -> None:
        previous = self.capture_spectrum_state()
        if self.reference_integral_id == int(item["id"]):
            self.reference_integral_id = None
        self.integrals.remove(item)
        self.remember_spectrum_state(previous, "удаление интеграла")
        self.draw()

    def open_phase_dialog(self) -> None:
        self.integral_boundary_editor.set_active(False)
        if self.current_comparison_id is not None:
            messagebox.showinfo(
                "Фазирование",
                "Для фазирования выберите отдельный спектр в списке слева.",
                parent=self,
            )
            return
        if self.real is None:
            messagebox.showinfo("Нет спектра", "Сначала загрузите спектр.", parent=self)
            return
        PhaseDialog(self)

    def open_correction_dialog(self) -> None:
        self.integral_boundary_editor.set_active(False)
        if self.current_comparison_id is not None:
            messagebox.showinfo(
                "Коррекция базовой линии",
                "Для коррекции выберите отдельный спектр в списке слева.",
                parent=self,
            )
            return
        if self.real is None:
            messagebox.showinfo("Нет спектра", "Сначала загрузите спектр.", parent=self)
            return
        CorrectionDialog(self)


from nmr_dialogs import CorrectionDialog, MultiSpectrumDialog, PhaseDialog, ShiftSpectrumDialog

if __name__ == "__main__":
    SpectrumApp().mainloop()
