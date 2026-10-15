"""Live local fit preview and independently named component-group integrals."""

from __future__ import annotations
import copy
import queue
import threading
import uuid
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.widgets import SpanSelector
from spin_backend import (
    data_snapshot,
    explain_fit_message,
    run_selected_region,
    same_state,
    model_component,
    model_component_areas,
    model_component_fwhms,
)
from spin_dialog import SpinParameters
from spin_history import WorkspaceHistory, snapshot
from spintegrate.profiles import SHAPE_LABELS
from spin_tuning import suggest_parameters
from ui_theme import PAGE, MUTED
from signal_quality import fit_quality
from ui_helpers import add_tooltip
from spin_groups import (
    component_matches,
    group_area,
    matching_tolerance,
    remap_groups,
    refresh_automatic_groups,
    validate_groups,
    recipe_for_groups,
    candidate_centers,
)


class SpinWorkspace(tk.Toplevel):
    COLORS = ("#e45756", "#4c78a8", "#59a14f", "#f28e2b", "#9467bd", "#17becf")

    def __init__(self, app, records, job, settings, offsets=None, initial=None):
        super().__init__(app)
        self.app, self.records, self.job = app, copy.deepcopy(records), dict(job)
        self.offsets = dict(offsets or {})
        self.states = copy.deepcopy(initial or {})
        self.running, self.closed = False, False
        self.revision = self.accepted_revision = 0
        self.pending = self.debounce = None
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.settings = copy.deepcopy(settings)
        self.active_sid = next(iter(records))
        self.group_specs = []
        self.groups_initialized = False
        self.refreshing = False
        self.history = WorkspaceHistory()
        self._selections = {}
        self._views = {}
        self._plotted_sid = None
        self.component_artists = {}
        self.group_artists = {}
        self.last_pick_event = None
        self.component_selector = None
        self.select_region = tk.BooleanVar(value=False)
        self.plot_mode = tk.StringVar(value="click")
        self.add_selection = tk.BooleanVar(value=False)
        self.show_components = tk.BooleanVar(value=True)
        self.selection_info = tk.StringVar(value="Выделите участок на графике")
        self.group_name = tk.StringVar(value="Интеграл 1")
        self._whole_area_group_id = None
        self._selection_anchor = None
        self.show_residual = tk.BooleanVar(value=False)
        self.parameters_visible = True
        self.tables_visible = True
        self.plot_focused = False
        self._layout_job = None
        self._layout_initialized = False
        app._spin_workspace = self
        app._spin_busy = app._spectrum_dialog_open = True
        app.restore_interaction_mode()
        self.title("Himera · Spintegrate — подгонка и итоговые интегралы")
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(1450, screen_width - 50)
        height = min(900, screen_height - 90)
        self.geometry(f"{width}x{height}+{max(0, (screen_width - width) // 2)}+20")
        self.minsize(min(900, width), min(620, height))
        # A normal resizable window exposes Windows' maximize button; grab keeps the edit modal.
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        title_row = ttk.Frame(body)
        title_row.pack(fill="x")
        ttk.Label(
            title_row,
            text=f"Область подгонки: {job['left']:.5f} … {job['right']:.5f} ppm",
            font="AppTitleFont",
        ).pack(side="left", fill="x", expand=True)
        self.fit_score = tk.StringVar(value="R²: —")
        score_label = ttk.Label(title_row, textvariable=self.fit_score, font="AppTitleFont")
        score_label.pack(side="left", padx=12)
        add_tooltip(
            score_label,
            "R² по всей выбранной области после локальной базовой линии.\n"
            "Это совпадение модели с сигналом, а не точность интегралов.\n"
            "Для постоянного сигнала R² не определён; плохая модель может дать отрицательный R².",
        )
        self.parameter_toggle = tk.Button(
            title_row, text="Скрыть параметры", command=self.toggle_parameters
        )
        self.parameter_toggle.pack(side="right", padx=3)
        self.focus_plot_button = tk.Button(
            title_row, text="Развернуть график", command=self.toggle_plot_focus
        )
        self.focus_plot_button.pack(side="right", padx=3)
        add_tooltip(
            self.focus_plot_button,
            "Увеличить график, свернув панели. F11 — переключить; Esc — вернуть панели.",
        )
        ttk.Label(
            body,
            text="1. Проверьте подгонку.   2. Выделите сигнал на графике.   3. Дайте имя и создайте интеграл.",
            wraplength=1100,
        ).pack(anchor="w", pady=5)
        panes = tk.PanedWindow(
            body,
            orient="horizontal",
            sashwidth=8,
            sashrelief="raised",
            showhandle=True,
            opaqueresize=True,
            bd=0,
            bg=PAGE,
        )
        self.panes = panes
        self.parameters = SpinParameters(panes, settings, self.parameters_changed, self.auto_start)
        self.parameters.set_baseline_context(
            self.records[self.active_sid], job["left"], job["right"], self.offset(self.active_sid)
        )
        self._parameter_width = 410
        panes.add(self.parameters, minsize=260, width=self._parameter_width, stretch="never")
        right = ttk.Frame(panes)
        self.right = right
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        panes.add(right, minsize=440, stretch="always")
        header = ttk.Frame(right)
        header.grid(row=0, column=0, sticky="ew")
        self.spectrum_box = ttk.Combobox(
            header, state="readonly", width=10, values=[r["name"] for r in records.values()]
        )
        self.spectrum_box.current(0)
        self.spectrum_box.pack(side="left", padx=(0, 5))
        self.spectrum_box.bind("<<ComboboxSelected>>", self.select_spectrum)
        tk.Button(header, text="Пересчитать", command=self.parameters_changed).pack(
            side="right", padx=5
        )
        self.plot_split = tk.PanedWindow(
            right,
            orient="vertical",
            sashwidth=8,
            sashrelief="raised",
            showhandle=True,
            opaqueresize=True,
            bd=0,
            bg=PAGE,
        )
        self.plot_split.grid(row=1, column=0, sticky="nsew")
        plot_pane = ttk.Frame(self.plot_split)
        self.plot_pane = plot_pane
        plot_pane.columnconfigure(0, weight=1)
        plot_pane.rowconfigure(0, weight=1)
        self.plot_split.add(plot_pane, minsize=250, stretch="always")
        self.figure = Figure(figsize=(7, 4), dpi=100)
        self.top, self.residual = self.figure.subplots(
            2, 1, sharex=True, gridspec_kw={"height_ratios": [3, 1]}
        )
        self.canvas = FigureCanvasTkAgg(self.figure, plot_pane)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self.canvas.mpl_connect("resize_event", self.schedule_plot_layout)
        toolbar = NavigationToolbar2Tk(self.canvas, plot_pane, pack_toolbar=False)
        self.navigation = toolbar
        # Keep Matplotlib navigation callbacks, expose compact actions in the View menu.
        toolbar.update()
        self.canvas.mpl_connect("pick_event", self.pick_component)
        self.canvas.mpl_connect("button_press_event", self.start_plot_selection)
        self.canvas.mpl_connect("button_release_event", self.finish_plot_click)
        controls = ttk.Frame(plot_pane)
        controls.grid(row=2, column=0, sticky="ew", pady=4)
        tools = ttk.Frame(header)
        tools.pack(side="left", fill="x", expand=True)
        choose_button = ttk.Radiobutton(
            tools,
            text="Клик",
            variable=self.plot_mode,
            value="click",
            command=self.set_selection_mode,
        )
        choose_button.pack(side="left")
        add_tooltip(
            choose_button,
            "Кликайте по пунктирным кривым, чтобы добавлять или снимать выбор линий и интегралов.",
        )
        region_button = ttk.Radiobutton(
            tools,
            text="Область",
            variable=self.plot_mode,
            value="region",
            command=self.set_selection_mode,
        )
        region_button.pack(side="left", padx=4)
        add_tooltip(region_button, "Выбор линий протягиванием области. Ctrl добавляет участки.")
        pan_button = ttk.Radiobutton(
            tools,
            text="Сдвиг",
            variable=self.plot_mode,
            value="pan",
            command=self.set_selection_mode,
        )
        pan_button.pack(side="left", padx=5)
        add_tooltip(pan_button, "Перемещать график мышью. Шкала спектра и интегралы не меняются.")
        view_button = tk.Menubutton(tools, text="Вид ▾", relief="raised", bd=1, padx=6, pady=3)
        view_button.pack(side="left", padx=4)
        view_menu = tk.Menu(view_button, tearoff=False)
        view_button.configure(menu=view_menu)
        view_menu.add_command(label="Уместить область", command=self.fit_plot)
        view_menu.add_command(label="Приблизить прямоугольником", command=self.zoom_plot)
        view_menu.add_command(label="Предыдущий вид", command=self.navigation.back)
        view_menu.add_command(label="Следующий вид", command=self.navigation.forward)
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="Показать остаток подгонки", variable=self.show_residual, command=self.layout_plot
        )
        view_menu.add_checkbutton(
            label="Показать отдельные линии модели",
            variable=self.show_components,
            command=self.plot,
        )
        view_menu.add_separator()
        view_menu.add_command(
            label="Сохранить изображение графика…", command=self.navigation.save_figure
        )
        selection_row = ttk.Frame(controls)
        selection_row.pack(fill="x", pady=3)
        ttk.Label(selection_row, textvariable=self.selection_info).pack(
            side="left", fill="x", expand=True
        )
        ttk.Checkbutton(selection_row, text="Добавлять к выбору", variable=self.add_selection).pack(
            side="left"
        )
        tk.Button(selection_row, text="Снять выбор", command=self.clear_component_selection).pack(
            side="left", padx=3
        )
        tk.Button(selection_row, text="Как выбрать?", command=self.show_selection_help).pack(
            side="right"
        )
        self.tables = ttk.Notebook(self.plot_split)
        self.plot_split.add(self.tables, minsize=165, height=205, stretch="never")
        self.output_tab, self.lines_tab, diagnostic_tab = (
            ttk.Frame(self.tables, padding=4) for _ in range(3)
        )
        self.tables.add(self.output_tab, text="Итоговые интегралы")
        self.tables.add(self.lines_tab, text="Линии модели")
        self.tables.add(diagnostic_tab, text="Качество подгонки")
        for tab in (self.output_tab, self.lines_tab):
            tab.rowconfigure(1, weight=1)
            tab.columnconfigure(0, weight=1)
        create_row = ttk.Frame(self.output_tab)
        create_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 3))
        ttk.Label(create_row, text="Имя:").pack(side="left")
        ttk.Entry(create_row, textvariable=self.group_name, width=18).pack(
            side="left", fill="x", expand=True, padx=4
        )
        self.create_button = tk.Button(
            create_row, text="Создать из выбранного", command=self.create_group, state="disabled"
        )
        self.create_button.pack(side="right")
        ttk.Label(
            self.lines_tab,
            text="Это отдельные линии аппроксимации, а не готовые интегралы. Ctrl/Shift — выбрать несколько.",
            wraplength=620,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self.components = ttk.Treeview(
            self.lines_tab,
            columns=("number", "center", "width", "area", "group", "shape"),
            show="headings",
            height=6,
            selectmode="extended",
        )
        for key, title, width in (
            ("number", "Линия №", 65),
            ("center", "Центр, ppm", 100),
            ("width", "FWHM, Гц", 90),
            ("area", "Площадь линии", 110),
            ("group", "Входит в интеграл", 150),
            ("shape", "Форма линии", 170),
        ):
            self.components.heading(key, text=title)
            self.components.column(key, width=width)
        self.groups = ttk.Treeview(
            self.output_tab,
            columns=("name", "count", "area", "shape"),
            show="headings",
            height=6,
            selectmode="browse",
        )
        for key, title, width in (
            ("name", "Итоговый интеграл", 180),
            ("count", "Линий внутри", 105),
            ("area", "Полная площадь", 130),
            ("shape", "Форма", 190),
        ):
            self.groups.heading(key, text=title)
            self.groups.column(key, width=width)
        self.components.bind("<<TreeviewSelect>>", lambda _: self.plot())
        self.groups.bind("<<TreeviewSelect>>", self.group_selection_changed)
        self.groups.bind("<Double-1>", self.select_group_members)
        output_actions = ttk.Frame(self.output_tab)
        output_actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        self.replace_button = tk.Button(
            output_actions,
            text="Заменить состав выбранным",
            command=self.assign_selected,
            state="disabled",
        )
        self.replace_button.pack(side="left")
        add_tooltip(
            self.replace_button,
            "Заменить линии в выделенном итоговом интеграле на текущий выбор на графике или в списке.",
        )
        self.group_actions = []
        for text, command in (
            ("Состав", self.select_group_members),
            ("Форма…", self.choose_group_shape),
            ("Имя…", self.rename_group),
            ("Удалить", self.delete_group),
        ):
            button = tk.Button(output_actions, text=text, command=command)
            button.pack(side="left", padx=2)
            self.group_actions.append(button)
        for table in (self.components, self.groups):
            table.grid(row=1, column=0, sticky="nsew")
            scroll = ttk.Scrollbar(table.master, orient="vertical", command=table.yview)
            scroll.grid(row=1, column=1, sticky="ns")
            horizontal = ttk.Scrollbar(table.master, orient="horizontal", command=table.xview)
            horizontal.grid(row=3, column=0, sticky="ew")

            def scroll_x(first, last, bar=horizontal):
                bar.set(first, last)
                if float(first) <= 0 and float(last) >= 1:
                    bar.grid_remove()
                else:
                    bar.grid()

            table.configure(yscrollcommand=scroll.set, xscrollcommand=scroll_x)
            table.bind(
                "<Control-c>",
                lambda event, t=table: app.copy_treeview_rows(t, context="spintegrate"),
            )
        self.status = tk.StringVar(value="Подготовка предпросмотра…")
        status_label = ttk.Label(body, textvariable=self.status, wraplength=900)
        self.notes = tk.Text(
            diagnostic_tab,
            height=5,
            wrap="word",
            relief="flat",
            state="disabled",
            bg=PAGE,
            fg=MUTED,
        )
        self.notes.pack(fill="both", expand=True)
        footer = ttk.Frame(body)
        footer.pack(side="bottom", fill="x")
        self.undo_button = tk.Button(
            footer, text="← Назад", command=self.undo_edit, state="disabled"
        )
        self.undo_button.pack(side="left")
        self.redo_button = tk.Button(
            footer, text="Вперёд →", command=self.redo_edit, state="disabled"
        )
        self.redo_button.pack(side="left", padx=4)
        add_tooltip(
            self.undo_button,
            "Отменить изменение в этом окне: Ctrl+Z. Также возвращает последнюю готовую подгонку при ошибке параметров.",
        )
        add_tooltip(self.redo_button, "Повторить изменение: Ctrl+Shift+Z.")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=80)
        self.progress.pack(side="left", padx=8)
        tk.Button(footer, text="Отмена", command=self.destroy).pack(side="right")
        self.apply_button = tk.Button(
            footer, text="Применить интегралы", command=self.apply, state="disabled"
        )
        self.apply_button.pack(side="right", padx=8)
        # Reserve footer/diagnostics first; only the central workspace may shrink.
        status_label.pack(side="bottom", fill="x", pady=7)
        panes.pack(fill="both", expand=True)
        self.poll_id = self.after(60, self.poll)
        self.bind("<Escape>", self.escape_workspace)
        self.bind("<F11>", lambda _event: self.toggle_plot_focus() or "break")
        self.bind("<Control-KeyPress>", self.history_key)
        self.after_idle(self.initialize_plot_size)
        if self.states:
            self.initialize_groups()
            self.refresh()
            self.remember_edit()
        else:
            self.parameters_changed()

    def offset(self, sid):
        return float(self.offsets.get(sid, self.offsets.get(str(sid), 0)))

    def history_key(self, event):
        # Windows physical Z also works with the Russian keyboard layout.
        if event.keysym.lower() in ("z", "cyrillic_ya") or event.keycode == 90:
            return self.redo_edit() if event.state & 1 else self.undo_edit()

    def remember_edit(self):
        if self.ready():
            self.history.push(
                snapshot(self.states, self.settings, self.group_specs, self._whole_area_group_id)
            )
        self.update_history_buttons()

    def update_history_buttons(self):
        pending = not self.ready() and self.history.current is not None
        self.undo_button.configure(
            state="normal" if pending or self.history.can_undo else "disabled"
        )
        self.redo_button.configure(
            state="normal" if self.ready() and self.history.can_redo else "disabled"
        )

    def restore_edit(self, item):
        if item is None:
            return
        # Invalidate any worker still returning the abandoned revision.
        self.revision += 1
        self.accepted_revision = self.revision
        self.pending = None
        if self.debounce:
            self.after_cancel(self.debounce)
            self.debounce = None
        restored = snapshot(item["states"], item["settings"], item["specs"], item["whole_area_id"])
        self.states, self.settings = restored["states"], restored["settings"]
        self.group_specs = restored["specs"]
        self._whole_area_group_id = restored["whole_area_id"]
        self.parameters.set_recipe(self.settings, notify=False)
        self._selections.clear()
        self.progress.stop()
        self.refresh(preserve_selection=False)
        self.update_history_buttons()

    def undo_edit(self):
        self.restore_edit(self.history.undo() if self.ready() else self.history.current)
        return "break"

    def redo_edit(self):
        if self.ready() and self.history.can_redo:
            self.restore_edit(self.history.redo())
        return "break"

    def fit_plot(self):
        self._views.pop(self.active_sid, None)
        self._plotted_sid = None
        self.plot()

    def zoom_plot(self):
        self.plot_mode.set("zoom")
        self.select_region.set(False)
        if getattr(self.navigation.mode, "name", "") != "ZOOM":
            self.navigation.zoom()
        self.configure_component_selector()
        self.update_selection_ui()

    def initialize_plot_size(self):
        if self.closed or self._layout_initialized or not self.states:
            return
        self.update_idletasks()
        if not self.canvas.get_tk_widget().winfo_ismapped():
            return
        self._layout_initialized = True
        height = self.plot_split.winfo_height()
        table_height = min(220, max(200, int(height * 0.28)))
        self.plot_split.sash_place(0, 0, max(250, height - table_height - 8))
        self.layout_plot()

    def schedule_plot_layout(self, _event=None):
        if self.closed:
            return
        if self._layout_job:
            self.after_cancel(self._layout_job)
        self._layout_job = self.after(40, self.layout_plot)

    def layout_plot(self):
        """Recompute pixel margins after the canvas, not its old requested size, changes."""
        if self.closed:
            return
        if self._layout_job:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        width, height = self.figure.bbox.width, self.figure.bbox.height
        left = min(0.20, 78 / max(width, 1))
        right = 1 - min(0.04, 18 / max(width, 1))
        bottom = min(0.25, 52 / max(height, 1))
        upper = 1 - min(0.12, 24 / max(height, 1))
        available = max(0.25, upper - bottom)
        if self.show_residual.get():
            residual_height = available * 0.17
            gap = min(0.09, 22 / max(height, 1))
            self.top.set_position(
                (
                    left,
                    bottom + residual_height + gap,
                    right - left,
                    available - residual_height - gap,
                )
            )
            self.residual.set_position((left, bottom, right - left, residual_height))
            self.residual.set_visible(True)
            self.top.tick_params(axis="x", labelbottom=False)
            self.top.set_xlabel("")
            self.residual.set_xlabel("Химический сдвиг, ppm")
        else:
            self.top.set_position((left, bottom, right - left, available))
            self.residual.set_visible(False)
            self.top.tick_params(axis="x", labelbottom=True)
            self.top.set_xlabel("Химический сдвиг, ppm")
        self.canvas.draw_idle()

    def toggle_parameters(self):
        if self.plot_focused:
            self.toggle_plot_focus()
        self.set_parameters_visible(not self.parameters_visible)

    def set_parameters_visible(self, visible):
        if visible == self.parameters_visible:
            return
        if visible:
            self.panes.add(
                self.parameters,
                before=self.right,
                minsize=260,
                width=self._parameter_width,
                stretch="never",
            )
        else:
            self._parameter_width = self.parameters.winfo_width()
            self.panes.forget(self.parameters)
        self.parameters_visible = visible
        self.parameter_toggle.configure(
            text="Скрыть параметры" if visible else "Показать параметры"
        )

    def toggle_plot_focus(self):
        if not self.plot_focused:
            self._focus_restore = dict(
                parameters=self.parameters_visible, table_height=self.tables.winfo_height()
            )
            self.set_parameters_visible(False)
            self.plot_split.forget(self.tables)
            self.tables_visible = False
            self.plot_focused = True
            self.focus_plot_button.configure(text="Вернуть панели")
        else:
            self.plot_split.add(
                self.tables,
                minsize=165,
                height=self._focus_restore["table_height"],
                stretch="never",
            )
            self.tables_visible = True
            self.set_parameters_visible(self._focus_restore["parameters"])
            self.plot_focused = False
            self.focus_plot_button.configure(text="Развернуть график")
        self.schedule_plot_layout()

    def escape_workspace(self, _event=None):
        if self.plot_focused:
            self.toggle_plot_focus()
            return "break"
        return self.clear_component_selection()

    def auto_start(self):
        try:
            recipe, note = suggest_parameters(
                self.records[self.active_sid],
                self.parameters.collect_recipe(),
                self.job["left"],
                self.job["right"],
                offset=self.offset(self.active_sid),
            )
            self.parameters.set_recipe(recipe, note)
        except (ValueError, RuntimeError, OverflowError) as error:
            self.parameters.feedback.set(str(error))

    def parameters_changed(self):
        if self.closed:
            return
        self.revision += 1
        self.pending = None
        self.apply_button.configure(state="disabled")
        self.create_button.configure(state="disabled")
        self.replace_button.configure(state="disabled")
        for button in self.group_actions:
            button.configure(state="disabled")
        self.status.set(
            "Пересчёт… На графике пока предыдущая подгонка; сохранение станет доступно после обновления."
        )
        self.fit_score.set("R²: — (пересчёт)")
        self.progress.start(12)
        self.update_history_buttons()
        if self.debounce:
            self.after_cancel(self.debounce)
        self.debounce = self.after(350, self.schedule)

    def schedule(self):
        self.debounce = None
        try:
            settings = self.parameters.collect_recipe()
        except (ValueError, OverflowError) as error:
            self.progress.stop()
            self.status.set(
                f"Проверьте параметры: {error} Предыдущая подгонка сохранена. «Назад» — вернуть её настройки."
            )
            return
        self.pending = (self.revision, settings)
        self.launch()

    def launch(self):
        if self.closed or self.running or self.pending is None:
            return
        revision, settings = self.pending
        self.pending = None
        self.running = True
        job, records, offsets = self.job, self.records, self.offsets
        events, stop = self.events, self.stop
        recipes = {sid: recipe_for_groups(settings, self.states.get(sid)) for sid in records}

        def worker():
            outputs, failures = {}, {}
            for sid, record in records.items():
                if stop.is_set():
                    return
                # Skip the rest of an obsolete series; the current solver finishes safely.
                if revision != self.revision:
                    break
                try:
                    offset = float(offsets.get(sid, offsets.get(str(sid), 0)))
                    outputs[sid] = run_selected_region(
                        record, recipes[sid], job["left"], job["right"], offset
                    )
                except Exception as error:
                    failures[sid] = str(error)
            events.put((revision, settings, outputs, failures))

        threading.Thread(target=worker, daemon=True, name="himera-live-fit").start()

    def poll(self):
        if self.closed:
            return
        try:
            revision, settings, outputs, failures = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.running = False
            if revision == self.revision:
                self.progress.stop()
                if failures:
                    self.status.set(
                        "Подгонка не готова: "
                        + "; ".join(f"{self.records[s]['name']}: {e}" for s, e in failures.items())
                        + " · «Назад» возвращает последнюю готовую подгонку."
                    )
                else:
                    self.remember_selection()
                    for sid, state in outputs.items():
                        if sid in self.states:
                            remap_groups(self.states[sid], state)
                    self.states, self.settings = outputs, settings
                    self.accepted_revision = revision
                    self.initialize_groups()
                    self.refresh(preserve_selection=False)
                    self.remember_edit()
            self.launch()
        self.poll_id = self.after(60, self.poll)

    def initialize_groups(self):
        first = next(
            (s for s in self.states.values() if s.get("groups")), next(iter(self.states.values()))
        )
        if not self.groups_initialized:
            self.groups_initialized = True
            self.group_specs = [dict(id=g["id"], name=g["name"]) for g in first.get("groups", [])]
            self._whole_area_group_id = next(
                (g["id"] for g in first.get("groups", []) if g.get("automatic", False)), None
            )
            if not self.group_specs:
                self.group_specs = [dict(id=uuid.uuid4().hex, name="Вся область")]
                self._whole_area_group_id = self.group_specs[0]["id"]
                for state in self.states.values():
                    state["groups"] = [
                        dict(
                            **self.group_specs[0],
                            components=list(range(len(state["models"][0]["centers"]))),
                            missing=0,
                            automatic=True,
                        )
                    ]
        reference_sid = next((sid for sid, state in self.states.items() if "groups" in state), None)
        if reference_sid is not None:
            reference = self.states[reference_sid]
            for sid, state in self.states.items():
                if "groups" in state:
                    continue
                matches = component_matches(
                    candidate_centers(reference) + self.offset(reference_sid),
                    candidate_centers(state) + self.offset(sid),
                    matching_tolerance(reference["models"][0]),
                )
                state["groups"] = [
                    dict(
                        id=g["id"],
                        name=g["name"],
                        components=[matches[i] for i in g["components"] if i in matches],
                        missing=g.get("missing", 0)
                        + sum(i not in matches for i in g["components"]),
                        **({"automatic": True} if g.get("automatic", False) else {}),
                        **({"line_shape": g["line_shape"]} if "line_shape" in g else {}),
                    )
                    for g in reference["groups"]
                ]
        for state in self.states.values():
            refresh_automatic_groups(state["groups"], len(state["models"][0]["centers"]))

    def select_spectrum(self, _event=None):
        self.remember_selection()
        self.active_sid = list(self.records)[self.spectrum_box.current()]
        self.parameters.set_baseline_context(
            self.records[self.active_sid],
            self.job["left"],
            self.job["right"],
            self.offset(self.active_sid),
        )
        self.refresh(preserve_selection=False)

    def remember_selection(self):
        state = self.states.get(self.active_sid)
        if state:
            indices = self.selected_indices()
            centers = candidate_centers(state)
            self._selections[self.active_sid] = centers[
                [i for i in indices if i < len(centers)]
            ].copy()

    def selected_indices(self):
        return [int(i) for i in self.components.selection()]

    def selected_group(self):
        selection = self.groups.selection()
        return selection[0] if selection else None

    def ready(self):
        return bool(self.states) and self.accepted_revision == self.revision

    def create_group(self):
        if not self.ready() or not self.selected_indices():
            self.status.set("Сначала выберите одну или несколько линий в таблице или на графике.")
            return
        name = self.group_name.get().strip()
        if not name:
            self.selection_info.set("Введите имя итогового интеграла, например «бутен-2».")
            return
        if any(
            g["name"] == name and g["id"] != self._whole_area_group_id for g in self.group_specs
        ):
            self.selection_info.set(
                "Такое имя уже есть. Введите другое имя или замените состав существующего интеграла."
            )
            return
        indices = self.selected_indices()
        # The initial whole-area result is a convenience, not an extra substance.
        # Creating user-defined outputs replaces this placeholder, leaving other lines unassigned.
        if self._whole_area_group_id is not None:
            gid = self._whole_area_group_id
            self.group_specs = [g for g in self.group_specs if g["id"] != gid]
            for state in self.states.values():
                state["groups"] = [g for g in state["groups"] if g["id"] != gid]
            self._whole_area_group_id = None
        spec = dict(id=uuid.uuid4().hex, name=name)
        self.add_group(spec, indices)
        self.group_name.set(f"Интеграл {len(self.group_specs) + 1}")
        self.select_group_members()

    def add_group(self, spec, indices):
        """Create a common output, with per-spectrum component membership."""
        self.group_specs.append(dict(spec))
        source = self.states[self.active_sid]["models"][0]
        anchors = candidate_centers(self.states[self.active_sid])[indices] + self.offset(
            self.active_sid
        )
        for sid, state in self.states.items():
            mapped = component_matches(
                anchors, candidate_centers(state) + self.offset(sid), matching_tolerance(source)
            )
            state["groups"].append(dict(**spec, components=[], missing=0))
            self.set_members(sid, spec["id"], list(mapped.values()), len(anchors) - len(mapped))
        self.refresh(group_id=spec["id"])
        if any(g.get("line_shape") for s in self.states.values() for g in s["groups"]):
            self.parameters_changed()
        else:
            self.remember_edit()

    def set_members(self, sid, group_id, indices, missing=0):
        for group in self.states[sid]["groups"]:
            if group["id"] == group_id:
                group.update(components=sorted(set(indices)), missing=missing)
            else:
                group["components"] = [i for i in group["components"] if i not in indices]

    def assign_selected(self):
        gid = self.selected_group()
        if self.ready() and gid:
            # Empty selection is an explicit zero for this spectrum; also acknowledges missing lines.
            self.set_members(self.active_sid, gid, self.selected_indices())
            if gid == self._whole_area_group_id:
                self._whole_area_group_id = None
                spec = next(s for s in self.group_specs if s["id"] == gid)
                spec["name"] = "Интеграл 1"
                for state in self.states.values():
                    group = next(g for g in state["groups"] if g["id"] == gid)
                    group["name"] = spec["name"]
                    group.pop("automatic", None)
                if self.group_name.get() == "Интеграл 1":
                    self.group_name.set("Интеграл 2")
            self.refresh(group_id=gid)
            if any(g.get("line_shape") for s in self.states.values() for g in s["groups"]):
                self.parameters_changed()
            else:
                self.remember_edit()

    def rename_group(self):
        if not self.ready():
            return
        gid = self.selected_group()
        spec = next((s for s in self.group_specs if s["id"] == gid), None)
        if not spec:
            return
        name = simpledialog.askstring(
            "Интеграл", "Название:", initialvalue=spec["name"], parent=self
        )
        if name and name.strip():
            if any(s["id"] != gid and s["name"] == name.strip() for s in self.group_specs):
                self.status.set("Такое имя интеграла уже есть. Выберите другое имя.")
                return
            if gid == self._whole_area_group_id:
                self._whole_area_group_id = None
                for state in self.states.values():
                    next(g for g in state["groups"] if g["id"] == gid).pop("automatic", None)
            spec["name"] = name.strip()
            for state in self.states.values():
                next(g for g in state["groups"] if g["id"] == gid)["name"] = spec["name"]
            self.refresh(group_id=gid)
            self.remember_edit()

    def delete_group(self):
        if not self.ready():
            return
        gid = self.selected_group()
        if not gid:
            return
        has_shapes = any(g.get("line_shape") for s in self.states.values() for g in s["groups"])
        self.group_specs = [s for s in self.group_specs if s["id"] != gid]
        if gid == self._whole_area_group_id:
            self._whole_area_group_id = None
        for state in self.states.values():
            state["groups"] = [g for g in state["groups"] if g["id"] != gid]
        self.refresh()
        if has_shapes:
            self.parameters_changed()
        else:
            self.remember_edit()

    def choose_group_shape(self):
        """Advanced opt-in: all lines of one chemical group share a shape family."""
        gid = self.selected_group()
        if not self.ready() or not gid:
            return
        group = next(g for g in self.states[self.active_sid]["groups"] if g["id"] == gid)
        window = tk.Toplevel(self)
        window.title(f"Расширенная модель · {group['name']}")
        window.transient(self)
        window.grab_set()
        body = ttk.Frame(window, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"Форма линий интеграла «{group['name']}»", font="AppTitleFont").pack(
            anchor="w"
        )
        inherited = "По общей настройке"
        value = tk.StringVar(value=SHAPE_LABELS.get(group.get("line_shape"), inherited))
        choices = (inherited, *SHAPE_LABELS.values())
        ttk.Combobox(body, textvariable=value, values=choices, state="readonly", width=38).pack(
            fill="x", pady=12
        )
        ttk.Label(
            body,
            wraplength=430,
            justify="left",
            text="Все компоненты выбранного интеграла будут подогнаны этой формой вместе с остальной областью. "
            "В серии выбор применяется к этому интегралу каждого спектра.\n\n"
            "Асимметричный профиль меняет левую и правую ширины. Voigt с дисперсией учитывает небольшую "
            "общую фазовую примесь; это не фазирование спектра.\n\n"
            "Площади пересчитаются. Проверяйте остаток и устойчивость результата, а не только R².",
        ).pack(anchor="w")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(14, 0))

        def apply():
            shape = next((s for s, label in SHAPE_LABELS.items() if label == value.get()), None)
            self.set_group_shape(gid, shape)
            close()

        def close():
            window.destroy()
            if not self.closed:
                self.grab_set()

        tk.Button(buttons, text="Отмена", command=close).pack(side="right")
        tk.Button(buttons, text="Пересчитать", command=apply).pack(side="right", padx=6)
        window.protocol("WM_DELETE_WINDOW", close)
        window.bind("<Escape>", lambda _event: close())

    def set_group_shape(self, gid, shape=None):
        if shape is not None and shape not in SHAPE_LABELS:
            raise ValueError("Неизвестная форма интеграла.")
        for state in self.states.values():
            for group in state.get("groups", []):
                if group["id"] == gid:
                    if shape is None:
                        group.pop("line_shape", None)
                    else:
                        group["line_shape"] = shape
        self.parameters_changed()

    def refresh(self, group_id=None, preserve_selection=True):
        if preserve_selection:
            self.remember_selection()
        self.refreshing = True
        group_id = group_id or self.selected_group()
        self.components.delete(*self.components.get_children())
        self.groups.delete(*self.groups.get_children())
        state = self.states.get(self.active_sid)
        if state:
            model = state["models"][0]
            fwhms = model_component_fwhms(model)
            areas = model_component_areas(model)
            line_shapes = model.get(
                "line_shapes",
                [state["recipe"]["fit"].get("line_shape", "lorentzian")] * len(model["centers"]),
            )
            membership = {i: g for g in state["groups"] for i in g["components"]}
            for index, (center, amp, width) in enumerate(
                zip(model["centers"], model["amplitudes"], model["widths"])
            ):
                group = membership.get(index)
                self.components.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        index + 1,
                        f"{center + self.offset(self.active_sid):.6f}",
                        f"{fwhms[index] * state['sf_mhz']:.4g}",
                        f"{areas[index]:.6g}",
                        group["name"] if group else "Не назначена",
                        SHAPE_LABELS[line_shapes[index]],
                    ),
                )
            matches = component_matches(
                self._selections.get(self.active_sid, []),
                candidate_centers(state),
                matching_tolerance(model),
            )
            self.components.selection_set([str(i) for i in matches.values()])
            for g in state["groups"]:
                value = group_area(
                    self.records[self.active_sid], state, g["id"], absolute=self.absolute()
                )
                self.groups.insert(
                    "",
                    "end",
                    iid=g["id"],
                    values=(
                        g["name"],
                        len(g["components"]),
                        "— (потеряны линии)" if not np.isfinite(value) else f"{value:.6g}",
                        SHAPE_LABELS.get(g.get("line_shape"), "Общая настройка"),
                    ),
                )
            if group_id and self.groups.exists(group_id):
                self.groups.selection_set(group_id)
            elif state["groups"]:
                self.groups.selection_set(state["groups"][0]["id"])
            self.summarize()
        self.refreshing = False
        self.plot()

    def absolute(self):
        return self.app.integration_method.get() == "Сумма модулей относительно 0"

    def summarize(self):
        missing = sum(g.get("missing", 0) for s in self.states.values() for g in s["groups"])
        state = self.states[self.active_sid]
        model = state["models"][0]
        unassigned = len(model["centers"]) - sum(len(g["components"]) for g in state["groups"])
        diagnostic = model["diagnostics"]
        quality_info = fit_quality(model)
        r2 = quality_info["r_squared"]
        score_text = "— (не определён)" if r2 is None else f"{100 * r2:.2f} %"
        self.fit_score.set(f"R²: {score_text}")
        count = len(self.group_specs)
        self.tables.tab(self.output_tab, text=f"Итоговые интегралы ({count})")
        self.tables.tab(self.lines_tab, text=f"Линии модели ({len(model['centers'])})")
        self.status.set(
            f"Линий модели: {len(model['centers'])}. Итоговых интегралов: {count}. Не включено линий: {unassigned}. "
            + (
                f"Потеряно линий в серии: {missing}. Проверьте состав — сохранение пока недоступно."
                if missing
                else "Линий не найдено. Проверьте порог поиска."
                if not len(model["centers"])
                else "Выберите пунктирные кривые → введите имя → создайте интеграл."
            )
        )
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        ratio = quality_info["noise_ratio"]
        ratio_text = f"{ratio:.3g}" if ratio is not None else "—"
        self.notes.insert(
            "end",
            f"Вычислений: {diagnostic.get('nfev', 0)} · порог: {state['threshold']:.5g}\n"
            + f"R²: {score_text} — по сигналу после локальной линии, не точность интегралов.\n"
            + f"Шум σ={quality_info['noise']:.4g}; RMSE={quality_info['rmse']:.4g}; "
            + f"RMSE/σ={ratio_text}; корреляция остатка={quality_info['autocorrelation']:.3f}\n"
            + "\n".join(
                explain_fit_message(note)
                for note in quality_info["warnings"] + model["notes"] + state["notes"]
            ),
        )
        self.notes.configure(state="disabled")
        self.apply_button.configure(
            state="normal" if self.ready() and self.group_specs and not missing else "disabled",
            text=f"Сохранить итоговые интегралы: {count}",
        )
        if not self.ready():
            self.fit_score.set("R²: — (предыдущая подгонка)")
            self.status.set(
                "Настройки ещё не применены. На графике предыдущая подгонка. Исправьте параметры или нажмите «Назад»."
            )

    def update_selection_ui(self):
        if self.refreshing:
            return
        state = self.states.get(self.active_sid)
        selected = self.selected_indices()
        total = 0.0
        if state and selected:
            model = state["models"][0]
            terms = model_component_areas(model)[selected]
            total = np.sum(np.abs(terms) if self.absolute() else terms)
        self.selection_info.set(
            f"Выбрано: {len(selected)} линий"
            if selected
            else "Кликните по пунктирной кривой"
            if self.plot_mode.get() == "click"
            else "Протяните прямоугольник для увеличения"
            if self.plot_mode.get() == "zoom"
            else "Перемещайте график мышью"
            if self.plot_mode.get() == "pan"
            else "Выберите участок на графике"
        )
        self.selected_area = total
        self.create_button.configure(state="normal" if self.ready() and selected else "disabled")
        gid = self.selected_group()
        group = (
            next((g for g in state.get("groups", []) if g["id"] == gid), None) if state else None
        )
        acknowledge_zero = bool(group and group.get("missing", 0) and not selected)
        self.replace_button.configure(
            text="Подтвердить отсутствие (0)" if acknowledge_zero else "Заменить состав выбранным",
            state="normal"
            if self.ready() and gid and (selected or acknowledge_zero)
            else "disabled",
        )
        for button in self.group_actions:
            button.configure(state="normal" if self.ready() and gid else "disabled")

    def group_selection_changed(self, _event=None):
        if not self.refreshing:
            self.plot()

    def select_group_members(self, _event=None):
        state = self.states.get(self.active_sid)
        gid = self.selected_group()
        if state and gid:
            group = next((g for g in state["groups"] if g["id"] == gid), None)
            if group:
                self.components.selection_set([str(i) for i in group["components"]])
                self.plot()

    def clear_component_selection(self):
        self.components.selection_set([])
        self.plot()
        return "break"

    def show_selection_help(self):
        window = tk.Toplevel(self)
        window.title("Spintegrate · Как получить нужные интегралы")
        window.transient(self)
        tk.Label(
            window,
            justify="left",
            wraplength=540,
            padx=16,
            pady=14,
            text=(
                "Линия модели — одна компонента выбранной формы. Один видимый сигнал или мультиплет может состоять из нескольких линий. Их число не равно числу веществ или интегралов.\n\n"
                "1. В режиме «Клик» нажимайте на пунктирные кривые: клик добавляет или снимает выбор. Клик по итоговому интегралу выбирает все его линии.\n"
                "   В режиме «Область» можно протянуть мышью участок. Выбранные линии подсветятся оранжевым.\n"
                "2. Для несоседних участков включите «Добавлять к выбору» или удерживайте Ctrl.\n"
                "3. Введите имя, например «бутан», и нажмите «Создать из выбранного». Появится одна строка итогового интеграла.\n"
                "4. Повторите для остальных сигналов. Первый пользовательский интеграл заменяет начальный интеграл «Вся область». Не включённые линии не учитываются.\n"
                "5. В таблице итогов двойной щелчок показывает состав. «Заменить состав» изменяет только выбранный интеграл текущего спектра. Создание нового интеграла сопоставляет линии во всей серии.\n\n"
                "Если линий неожиданно много, повысьте порог поиска и проверьте остаток подгонки. Шум тоже может давать кандидатов."
            ),
        ).pack(fill="both", expand=True)
        tk.Button(window, text="Понятно", command=window.destroy).pack(pady=(0, 12))

    def plot(self):
        if self.refreshing or not self.states.get(self.active_sid):
            return
        self.update_selection_ui()
        state = self.states[self.active_sid]
        model = state["models"][0]
        if self._plotted_sid is not None and self.top.has_data():
            self._views[self._plotted_sid] = (
                self.top.get_xlim(),
                self.top.get_ylim(),
                self.residual.get_ylim(),
            )
        view = self._views.get(self.active_sid)
        self._plotted_sid = self.active_sid
        if self.component_selector is not None:
            self.component_selector.disconnect_events()
            self.component_selector = None
        self.top.clear()
        self.residual.clear()
        self.component_artists.clear()
        self.group_artists.clear()
        x = model["x"] + self.offset(self.active_sid)
        self.top.plot(
            x, model["corrected"], color="#4c78a8", lw=1, label="Сигнал после локальной линии"
        )
        self.top.plot(x, model["fitted"], color="#f28e2b", ls="--", lw=1.4, label="Общая подгонка")
        selected = set(self.selected_indices())
        group_colors = {
            s["id"]: self.COLORS[n % len(self.COLORS)] for n, s in enumerate(self.group_specs)
        }
        membership = {
            i: g["id"]
            for g in state["groups"]
            if not g.get("automatic", False)
            for i in g["components"]
        }
        chosen = self.selected_group()
        for i, (center, amp, width) in enumerate(
            zip(model["centers"], model["amplitudes"], model["widths"])
        ):
            if not self.show_components.get():
                continue
            y = model_component(model, i)
            (artist,) = self.top.plot(
                x,
                y,
                color=group_colors.get(membership.get(i), "#83909f"),
                alpha=1 if i in selected or membership.get(i) == chosen else 0.8,
                lw=2 if i in selected else 0.9,
                linestyle="--",
                picker=5,
            )
            self.component_artists[artist] = i
        if selected:
            curve = sum(
                (model_component(model, i) for i in selected),
                np.zeros(len(x)),
            )
            self.top.fill_between(x, 0, curve, color="#f28e2b", alpha=0.25, label="Выбранные линии")
            self.top.plot(x, curve, color="#f28e2b", lw=2)
        for g in state["groups"]:
            if not g["components"] or g.get("automatic", False):
                continue
            curve = sum(
                (model_component(model, i) for i in g["components"]),
                np.zeros(len(x)),
            )
            (artist,) = self.top.plot(
                x,
                curve,
                color=group_colors[g["id"]],
                lw=2 if all(i in selected for i in g["components"]) else 1.5,
                linestyle="--",
                label=g["name"],
                picker=5,
            )
            if g["id"] != self._whole_area_group_id:
                self.group_artists[artist] = g["id"]
        self.residual.plot(x, model["corrected"] - model["fitted"], color="#68707d", lw=0.8)
        noise = fit_quality(model)["noise"]
        self.residual.axhspan(-2 * noise, 2 * noise, alpha=0.12, color="#59a14f", label="±2σ шума")
        self.residual.axhline(0, color="#b7c1cd", lw=0.6)
        self.residual.set_ylabel("Остаток")
        self.residual.set_xlabel("Химический сдвиг, ppm")
        if view:
            self.top.set_xlim(*view[0])
            self.top.set_ylim(*view[1])
            self.residual.set_ylim(*view[2])
        else:
            self.top.set_xlim(float(np.max(x)), float(np.min(x)))
        for ax in (self.top, self.residual):
            ax.grid(alpha=0.18)
        self.top.legend(loc="best", fontsize=8)
        self.layout_plot()
        self.configure_component_selector()
        self.canvas.draw_idle()
        if not self._layout_initialized:
            self.after_idle(self.initialize_plot_size)

    def configure_component_selector(self):
        if self.component_selector is None:
            self.component_selector = SpanSelector(
                self.top,
                self.select_component_region,
                "horizontal",
                useblit=False,
                button=1,
                props=dict(facecolor="#0078d7", alpha=0.15),
            )
        self.component_selector.set_active(self.select_region.get() and not self.navigation.mode)

    def set_selection_mode(self):
        self.select_region.set(self.plot_mode.get() == "region")
        if self.plot_mode.get() != "pan":
            if getattr(self.navigation.mode, "name", "") == "PAN":
                self.navigation.pan()
            elif getattr(self.navigation.mode, "name", "") == "ZOOM":
                self.navigation.zoom()
        elif getattr(self.navigation.mode, "name", "") != "PAN":
            self.navigation.pan()
        self.configure_component_selector()
        self.update_selection_ui()

    def start_plot_selection(self, event):
        self._selection_anchor = None
        self._selection_is_click = False
        self._drag_add = bool(event.key and ("ctrl" in event.key or "control" in event.key))
        if self.last_pick_event is event:
            return
        if (
            event.inaxes is self.top
            and event.button == 1
            and self.plot_mode.get() != "pan"
            and not self.navigation.mode
            and self.ready()
        ):
            self._selection_anchor = (event.x, event.y)

    def finish_plot_click(self, event):
        anchor, self._selection_anchor = self._selection_anchor, None
        if anchor is None or event.inaxes is not self.top or event.xdata is None:
            return
        if abs(event.x - anchor[0]) > 5 or abs(event.y - anchor[1]) > 5:
            return  # SpanSelector handles the actual drag range.
        self._selection_is_click = True
        model = self.states[self.active_sid]["models"][0]
        centers = np.asarray(model["centers"]) + self.offset(self.active_sid)
        if not len(centers):
            return
        index = int(np.argmin(np.abs(centers - event.xdata)))
        pixel_x = self.top.transData.transform((centers[index], 0))[0]
        curve_y = np.interp(
            event.xdata, model["x"] + self.offset(self.active_sid), model["corrected"]
        )
        pixel_y = self.top.transData.transform((event.xdata, curve_y))[1]
        if abs(pixel_x - event.x) > 14 or abs(pixel_y - event.y) > 18:
            if not self.add_selection.get() and not self._drag_add:
                self.clear_component_selection()
            return
        iid = str(index)
        if self.add_selection.get() or self._drag_add:
            if iid in self.components.selection():
                self.components.selection_remove(iid)
            else:
                self.components.selection_add(iid)
        else:
            self.components.selection_set(iid)
        self.plot()

    def select_component_region(self, low, high):
        if not self.ready() or low == high or getattr(self, "_selection_is_click", False):
            return
        centers = np.asarray(self.states[self.active_sid]["models"][0]["centers"]) + self.offset(
            self.active_sid
        )
        selected = [str(i) for i, c in enumerate(centers) if min(low, high) <= c <= max(low, high)]
        if self.add_selection.get() or getattr(self, "_drag_add", False):
            selected = list(dict.fromkeys((*self.components.selection(), *selected)))
        self.components.selection_set(selected)
        self.plot()

    def pick_component(self, event):
        if (
            self.select_region.get()
            or self.plot_mode.get() != "click"
            or self.navigation.mode
            or not self.ready()
            or event.mouseevent.button != 1
        ):
            return
        if self.last_pick_event is event.mouseevent:
            return
        if event.artist in self.component_artists or event.artist in self.group_artists:
            self.last_pick_event = event.mouseevent
            # Matplotlib emits several picks for overlapping lines. Choose only the closest.
            mouse = event.mouseevent
            if mouse.xdata is not None and mouse.ydata is not None:

                def distance(pair):
                    artist, _ = pair
                    order = np.argsort(artist.get_xdata())
                    y = np.interp(
                        mouse.xdata,
                        np.asarray(artist.get_xdata())[order],
                        np.asarray(artist.get_ydata())[order],
                    )
                    _, pixel_y = self.top.transData.transform((mouse.xdata, y))
                    return abs(pixel_y - mouse.y)

                candidates = list(self.group_artists.items()) + list(self.component_artists.items())
                artist, index = min(candidates, key=distance)
                # A group may overlap its component within a couple of screen pixels.
                # Prefer the displayed integral rather than selecting a random member.
                near_groups = [
                    pair
                    for pair in self.group_artists.items()
                    if distance(pair) <= distance((artist, index)) + 2
                ]
                if near_groups:
                    artist, index = min(near_groups, key=distance)
            else:
                artist = event.artist
                index = self.group_artists.get(artist, self.component_artists.get(artist))
            if artist in self.group_artists:
                group = next(g for g in self.states[self.active_sid]["groups"] if g["id"] == index)
                ids = [str(i) for i in group["components"]]
                self.groups.selection_set(index)
            else:
                ids = [str(index)]
            if all(iid in self.components.selection() for iid in ids):
                self.components.selection_remove(*ids)
            else:
                self.components.selection_add(*ids)
            if ids:
                self.components.see(ids[0])
            self.plot()

    def apply(self):
        if not self.ready() or not self.group_specs:
            return
        self.app.save_active_spectrum()
        comparison = self.app.comparisons.get(self.job["comparison_id"])
        context_valid = self.app.current_comparison_id == self.job["comparison_id"]
        if comparison:
            context_valid &= set(comparison["spectrum_ids"]) == set(self.records) and same_state(
                comparison.get("offsets", {}), self.offsets
            )
        else:
            context_valid &= (
                self.job["comparison_id"] is None and self.app.active_spectrum_id in self.records
            )
        if not context_valid:
            messagebox.showerror(
                "Spintegrate",
                "График или его сдвиги изменились. Откройте подгонку заново.",
                parent=self,
            )
            return
        for sid, state in self.states.items():
            validate_groups(state)
            if any(g.get("missing", 0) for g in state["groups"]):
                return
            current = self.app.spectra.get(sid)
            if current is None or not same_state(
                data_snapshot(current), data_snapshot(self.records[sid])
            ):
                messagebox.showerror(
                    "Spintegrate",
                    "Спектр изменился во время предпросмотра. Откройте подгонку заново.",
                    parent=self,
                )
                return
        outputs, job = copy.deepcopy(self.states), dict(self.job)
        job["groups"] = copy.deepcopy(self.group_specs)
        self.app.spin_settings = copy.deepcopy(self.settings)
        self.destroy()
        self.app.apply_spin_region(outputs, job)

    def destroy(self):
        if self.closed:
            return
        self.closed = True
        if self.component_selector is not None:
            self.component_selector.disconnect_events()
        self.stop.set()
        for callback in (self.debounce, self.poll_id):
            if callback:
                self.after_cancel(callback)
        if self._layout_job:
            self.after_cancel(self._layout_job)
        self.app._spin_busy = self.app._spectrum_dialog_open = False
        self.app._spin_workspace = None
        self.app.restore_interaction_mode()
        super().destroy()
