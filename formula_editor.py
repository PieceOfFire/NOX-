"""Advanced visual formula editor. The calculation document lives outside the window."""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from ui_helpers import add_tooltip
from ui_theme import (
    PAGE,
    SURFACE,
    TEXT,
    MUTED,
    BORDER,
    GRID,
    BUTTON_SURFACE,
    BUTTON_HOVER,
    BUTTON_PRESSED,
    ERROR,
    WARNING,
    SUCCESS,
    button as desktop_button,
    formula_palette,
)
from formula_engine import OPERATIONS, UNARY_OPERATIONS, format_value
from formula_canvas import FormulaCanvas
from formula_layout import block_size, clean_document, route_orthogonal, segments, wire_midpoint


class FormulaEditor(tk.Toplevel):
    add_formula_tooltip = staticmethod(add_tooltip)
    _OPERATION_NAMES = OPERATIONS
    _UNARY_OPERATIONS = UNARY_OPERATIONS

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.document = app.formula_document
        self.title("NOX · Редактор формул")
        width = min(1250, max(850, self.winfo_screenwidth() - 60))
        height = min(760, max(520, self.winfo_screenheight() - 100))
        x = max(0, min(app.winfo_rootx() + 20, self.winfo_screenwidth() - width - 20))
        y = max(0, min(app.winfo_rooty() + 20, self.winfo_screenheight() - height - 80))
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(850, 520)
        self.formula_tab = tk.Frame(self)
        self.formula_tab.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._initial_scene_pending = True
        self.build_formula_workspace()
        self.bind("<Configure>", self.on_editor_resize, add="+")

    def close(self):
        for gesture in (self._formula_drag, self._frame_drag, self._comment_drag):
            if isinstance(gesture, dict) and isinstance(gesture.get("history"), dict):
                self.remember_formula_state(gesture["history"], "перемещён элемент схемы")
        self.document.zoom = self.formula_canvas.zoom
        self.app.formula_editor = None
        self.destroy()

    def on_editor_resize(self, event):
        if event.widget is not self:
            return
        if event.width < 1050 and self.results_sidebar.winfo_manager():
            self.results_sidebar.pack_forget()
        elif event.width >= 1050 and not self.results_sidebar.winfo_manager():
            self.results_sidebar.pack(side="right", fill="y", padx=(10, 0))

    def reset_interaction(self):
        self.selected_formula_blocks.clear()
        self._formula_drag = self._frame_drag = self._comment_drag = None
        self._wire_drag = self._wire_handle_drag = self._selection_drag = None
        self._selection_rect = None
        self._formula_pan = False
        self.formula_canvas.delete("selection_marquee")
        self.formula_canvas.zoom = self.document.zoom
        self.zoom_label.set(f"{self.document.zoom:.0%}")

    def new_formula(self):
        previous = self.capture_formula_state()
        self.document.clear()
        self.reset_interaction()
        self.refresh_formula_workspace()
        self.remember_formula_state(previous, "создана новая схема")

    def clean_formula_scene(self):
        if not (self.formula_blocks or self.formula_frames or self.formula_comments):
            self.formula_status.set("Добавьте блоки — Clean упорядочит схему и проводки.")
            return
        previous = self.capture_formula_state()
        self.reset_interaction()
        clean_document(self.document)
        self.refresh_formula_workspace()
        self.fit_formula_scene()
        self.remember_formula_state(previous, "Clean: упорядочена схема")
        self.formula_status.set(
            "Clean: блоки разнесены, проводки перестроены. Ctrl+Z — вернуть расположение."
        )

    def scene_bounds(self):
        boxes = []
        for bid, block in self.formula_blocks.items():
            if self.hidden_formula_block(bid):
                continue
            w, h = self.formula_block_size(block)
            boxes.append(
                (float(block["x"]), float(block["y"]), float(block["x"]) + w, float(block["y"]) + h)
            )
        for frame in self.formula_frames.values():
            if frame.get("collapsed"):
                w, h = self.formula_frame_compact_size(frame)
                boxes.append(
                    (
                        float(frame["x"]),
                        float(frame["y"]),
                        float(frame["x"]) + w,
                        float(frame["y"]) + h,
                    )
                )
            else:
                x, y, w, h = self.formula_frame_bounds(frame)
                boxes.append((x, y, x + w, y + h))
        boxes.extend(
            (float(c["x"]), float(c["y"]), float(c["x"]) + 170, float(c["y"]) + 50)
            for c in self.formula_comments.values()
        )
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def fit_formula_scene(self):
        self.formula_canvas.update_idletasks()
        bounds = self.scene_bounds()
        if not bounds:
            return self.set_formula_zoom(1.0)
        left, top, right, bottom = bounds
        zoom = min(
            self.formula_canvas.winfo_width() / max(120, right - left + 100),
            self.formula_canvas.winfo_height() / max(120, bottom - top + 100),
            1.0,
        )
        self.set_formula_zoom(zoom)
        self.formula_canvas.center_at((left + right) / 2, (top + bottom) / 2)

    def set_formula_zoom(self, value, anchor=None):
        canvas = self.formula_canvas
        px, py = anchor or (canvas.winfo_width() / 2, canvas.winfo_height() / 2)
        x, y = canvas.canvasx(px), canvas.canvasy(py)
        canvas.zoom = max(0.2, min(2.5, float(value)))
        self.document.zoom = canvas.zoom
        self.zoom_label.set(f"{canvas.zoom:.0%}")
        self.draw_formula_grid()
        self.render_formula_blocks()
        canvas.center_at(
            x + (canvas.winfo_width() / 2 - px) / canvas.zoom,
            y + (canvas.winfo_height() / 2 - py) / canvas.zoom,
        )

    def on_formula_zoom(self, event):
        direction = 1 if getattr(event, "delta", 0) > 0 or getattr(event, "num", None) == 4 else -1
        self.set_formula_zoom(
            self.formula_canvas.zoom * (1.12 if direction > 0 else 1 / 1.12), (event.x, event.y)
        )
        return "break"

    def capture_formula_state(self):
        return self.app.capture_formula_state()

    def remember_formula_state(self, previous, action):
        self.app.remember_formula_state(previous, action)

    def formula_value_for_record(self, block_id, record, visited=None):
        return self.app.formula_engine_for_record(record).evaluate(block_id).value

    def formula_value(self, block_id, visited=None):
        record = self.spectra.get(self.active_spectrum_id)
        return self.formula_value_for_record(block_id, record)

    format_formula_value = staticmethod(format_value)

    def formula_preset_data(self):
        return self.app.formula_preset_data()

    def save_formula_preset(self):
        self.app.save_formula_preset(parent=self)

    def load_formula_preset(self, preset=None, *, show_error=True):
        return self.app.load_formula_preset(preset, show_error=show_error, parent=self)

    def ordered_integrals(self):
        return self.app.ordered_integrals()

    def integral_by_id(self, integral_id):
        return self.app.integral_by_id(integral_id)

    def integral_number(self, integral_id):
        return self.app.integral_number(integral_id)

    def integral_area(self, integral):
        return self.app.integral_area(integral)

    def comparison_record(self):
        return self.app.calculation_comparison_record()

    def record_integral_area(self, *args, **kwargs):
        return self.app.record_integral_area(*args, **kwargs)

    def copy_treeview_rows(self, *args, **kwargs):
        return self.app.copy_treeview_rows(*args, **kwargs)

    @property
    def active_spectrum_id(self):
        return self.app.active_spectrum_id

    @property
    def spectra(self):
        return self.app.spectra

    @property
    def comparisons(self):
        return self.app.comparisons

    @property
    def status(self):
        return self.app.status

    @property
    def formula_blocks(self):
        return self.document.blocks

    @formula_blocks.setter
    def formula_blocks(self, value):
        self.document.blocks = value

    @property
    def formula_frames(self):
        return self.document.frames

    @formula_frames.setter
    def formula_frames(self, value):
        self.document.frames = value

    @property
    def formula_comments(self):
        return self.document.comments

    @formula_comments.setter
    def formula_comments(self, value):
        self.document.comments = value

    @property
    def wire_routes(self):
        return self.document.wire_routes

    @wire_routes.setter
    def wire_routes(self, value):
        self.document.wire_routes = value

    @property
    def _next_formula_id(self):
        return self.document.next_block_id

    @_next_formula_id.setter
    def _next_formula_id(self, value):
        self.document.next_block_id = value

    @property
    def _next_formula_frame_id(self):
        return self.document.next_frame_id

    @_next_formula_frame_id.setter
    def _next_formula_frame_id(self, value):
        self.document.next_frame_id = value

    @property
    def _next_formula_comment_id(self):
        return self.document.next_comment_id

    @_next_formula_comment_id.setter
    def _next_formula_comment_id(self, value):
        self.document.next_comment_id = value

    def build_formula_workspace(self) -> None:
        """Создать визуальный редактор расчётов с акцентом на схему, а не на окна."""
        self.formula_theme = formula_palette()
        self.configure_formula_styles()
        header = tk.Frame(self.formula_tab, bg=PAGE, padx=10, pady=8)
        header.pack(fill="x")
        title_group = tk.Frame(header, bg=self.formula_theme["header"])
        title_group.pack(side="left")
        tk.Label(
            title_group,
            text="Редактор формул",
            font="AppTitleFont",
            bg=self.formula_theme["header"],
            fg=TEXT,
        ).pack(anchor="w")
        tk.Label(
            title_group,
            text="Визуальный расчёт по интегралам",
            font="TkDefaultFont",
            bg=self.formula_theme["header"],
            fg=MUTED,
        ).pack(anchor="w", pady=(1, 0))
        self.formula_health = tk.StringVar(value="Новая схема")
        self.formula_health_label = tk.Label(
            header,
            textvariable=self.formula_health,
            bg=PAGE,
            fg=MUTED,
            font="TkDefaultFont",
            padx=9,
            pady=5,
        )
        self.formula_health_label.pack(side="right", padx=(10, 0))
        self.make_formula_button(
            header, "Загрузить", self.load_formula_preset, variant="dark"
        ).pack(side="right")
        self.make_formula_button(
            header, "Сохранить", self.save_formula_preset, variant="primary"
        ).pack(side="right", padx=(0, 6))
        self.make_formula_button(header, "Новая", self.new_formula, variant="dark").pack(
            side="right", padx=(0, 6)
        )
        self.formula_status = tk.StringVar(
            master=self,
            value="Нажмите блок в библиотеке, соедините порты и добавьте результат.",
        )
        layout = tk.Frame(self.formula_tab, bg=PAGE, padx=10, pady=8)

        palette = tk.LabelFrame(
            layout,
            text="  Библиотека  ",
            bg=self.formula_theme["panel"],
            fg=self.formula_theme["ink"],
            font="TkDefaultFont",
            padx=8,
            pady=8,
            width=240,
            highlightbackground=self.formula_theme["line"],
            highlightthickness=0,
            bd=2,
            relief="groove",
        )
        palette.pack(side="left", fill="y", padx=(0, 10))
        palette.pack_propagate(False)
        palette_body = tk.Frame(palette, bg=self.formula_theme["panel"])
        palette_body.pack(fill="both", expand=True)
        palette_scroll = tk.Scrollbar(palette_body, orient="vertical")
        palette_scroll.pack(side="right", fill="y")
        self.palette_canvas = tk.Canvas(
            palette_body,
            bg=self.formula_theme["panel"],
            highlightthickness=0,
            width=195,
            yscrollcommand=palette_scroll.set,
        )
        self.palette_canvas.pack(side="left", fill="both", expand=True)
        palette_scroll.configure(command=self.palette_canvas.yview)
        self.palette_canvas.bind("<MouseWheel>", self.scroll_formula_palette)

        workspace = tk.LabelFrame(
            layout,
            text="  Рабочая область  ",
            bg=self.formula_theme["panel"],
            fg=self.formula_theme["ink"],
            font="TkDefaultFont",
            padx=0,
            pady=0,
            highlightbackground=self.formula_theme["line"],
            highlightthickness=0,
            bd=2,
            relief="groove",
        )
        workspace.pack(side="left", fill="both", expand=True)
        toolbar = tk.Frame(workspace, bg=self.formula_theme["toolbar"], padx=10, pady=7)
        toolbar.pack(fill="x")
        tk.Label(
            toolbar,
            text="Инструменты:",
            bg=self.formula_theme["toolbar"],
            fg=MUTED,
            font="AppSmallFont",
        ).pack(side="left", padx=(2, 8))
        self.formula_tool_buttons: dict[str, tk.Button] = {}
        for tool, icon, label in (
            ("pan", "✥", "Перемещение поля"),
            ("select", "▣", "Выделение блоков"),
            ("comment", "✎", "Комментарий"),
        ):
            button = tk.Button(
                toolbar,
                text=icon,
                width=3,
                relief="raised",
                bd=1,
                font=("Segoe UI Symbol", 10, "bold"),
                bg=BUTTON_SURFACE,
                activebackground=BUTTON_HOVER,
                fg=TEXT,
                command=lambda value=tool: self.set_formula_tool(value),
            )
            button.pack(side="left", padx=2)
            self.formula_tool_buttons[tool] = button
            self.add_formula_tooltip(button, label)
        self.make_formula_button(
            toolbar, "Удалить", self.delete_selected_formula_blocks, variant="quiet"
        ).pack(side="left", padx=(10, 2))
        tk.Label(
            toolbar,
            text="Del",
            bg=self.formula_theme["toolbar"],
            fg=MUTED,
            font="AppSmallFont",
        ).pack(side="left")
        self.make_formula_button(toolbar, "Фрейм", self.create_formula_frame, variant="quiet").pack(
            side="left", padx=(10, 2)
        )
        self.make_formula_button(toolbar, "⌖", self.focus_formula_scene, variant="icon").pack(
            side="right", padx=(4, 0)
        )
        self.add_formula_tooltip(toolbar.winfo_children()[-1], "Центрировать схему")
        self.make_formula_button(
            toolbar, "Снять выбор", self.clear_formula_selection, variant="quiet"
        ).pack(side="right")
        viewbar = tk.Frame(workspace, bg=self.formula_theme["toolbar"], padx=10, pady=4)
        viewbar.pack(fill="x")
        self.zoom_label = tk.StringVar(value=f"{self.document.zoom:.0%}")
        self.make_formula_button(
            viewbar,
            "−",
            lambda: self.set_formula_zoom(self.formula_canvas.zoom / 1.2),
            variant="icon",
        ).pack(side="left")
        tk.Label(
            viewbar,
            textvariable=self.zoom_label,
            bg=self.formula_theme["toolbar"],
            fg=TEXT,
            width=5,
        ).pack(side="left")
        self.make_formula_button(
            viewbar,
            "+",
            lambda: self.set_formula_zoom(self.formula_canvas.zoom * 1.2),
            variant="icon",
        ).pack(side="left")
        self.make_formula_button(
            viewbar, "100%", lambda: self.set_formula_zoom(1), variant="quiet"
        ).pack(side="left", padx=5)
        self.make_formula_button(
            viewbar, "Уместить схему", self.fit_formula_scene, variant="quiet"
        ).pack(side="left", padx=5)
        self.clean_button = self.make_formula_button(
            viewbar, "Clean", self.clean_formula_scene, variant="quiet"
        )
        self.clean_button.pack(side="left", padx=5)
        self.add_formula_tooltip(
            self.clean_button,
            "Упорядочить всю схему: убрать наложения и проложить аккуратные проводки. Ctrl+Z — отмена.",
        )
        tk.Label(
            viewbar,
            text="Масштаб: колесо",
            bg=self.formula_theme["toolbar"],
            fg=MUTED,
            font="AppSmallFont",
        ).pack(side="right")
        workspace_body = tk.Frame(workspace, bg=self.formula_theme["panel"])
        workspace_body.pack(fill="both", expand=True)
        workspace_body.rowconfigure(0, weight=1)
        workspace_body.columnconfigure(0, weight=1)
        formula_x_scroll = tk.Scrollbar(workspace_body, orient="horizontal")
        formula_y_scroll = tk.Scrollbar(workspace_body, orient="vertical")
        self.formula_canvas = FormulaCanvas(
            workspace_body,
            bg=SURFACE,
            highlightthickness=0,
            scrollregion=(0, 0, 1800, 1200),
            xscrollcommand=formula_x_scroll.set,
            yscrollcommand=formula_y_scroll.set,
        )
        self.formula_canvas.grid(row=0, column=0, sticky="nsew")
        formula_y_scroll.grid(row=0, column=1, sticky="ns")
        formula_x_scroll.grid(row=1, column=0, sticky="ew")
        formula_x_scroll.configure(command=self.formula_canvas.xview)
        formula_y_scroll.configure(command=self.formula_canvas.yview)
        self.formula_canvas.bind("<Button-3>", self.formula_menu)
        self.formula_canvas.bind("<ButtonPress-1>", self.start_formula_pan, add="+")
        self.formula_canvas.bind("<B1-Motion>", self.drag_formula_wire, add="+")
        self.formula_canvas.bind("<B1-Motion>", self.drag_formula_pan, add="+")
        self.formula_canvas.bind("<ButtonRelease-1>", self.drop_formula_wire, add="+")
        self.formula_canvas.bind("<ButtonRelease-1>", self.stop_formula_pan, add="+")
        self.formula_canvas.bind("<Configure>", self.on_formula_canvas_configure)
        self.formula_canvas.bind("<Delete>", self.delete_selected_formula_blocks)
        self.formula_canvas.bind("<Escape>", lambda _event: self.clear_formula_selection())
        self.formula_canvas.bind(
            "<KeyPress-v>", lambda _event: self.set_formula_tool("select") or "break"
        )
        self.formula_canvas.bind(
            "<KeyPress-V>", lambda _event: self.set_formula_tool("select") or "break"
        )
        self.formula_canvas.bind(
            "<KeyPress-c>", lambda _event: self.set_formula_tool("comment") or "break"
        )
        self.formula_canvas.bind(
            "<KeyPress-C>", lambda _event: self.set_formula_tool("comment") or "break"
        )
        self.formula_canvas.bind(
            "<KeyPress-space>", lambda _event: self.set_formula_tool("pan") or "break"
        )
        self.formula_canvas.bind("<Home>", lambda _event: self.focus_formula_scene() or "break")
        self.formula_canvas.bind("<MouseWheel>", self.on_formula_zoom)
        self.formula_canvas.bind("<Button-4>", self.on_formula_zoom)
        self.formula_canvas.bind("<Button-5>", self.on_formula_zoom)
        self.formula_canvas.zoom = self.document.zoom
        self._formula_world = (1800.0, 1200.0)

        results = tk.LabelFrame(
            layout,
            text="  Результаты  ",
            bg=self.formula_theme["panel"],
            fg=self.formula_theme["ink"],
            font="TkDefaultFont",
            padx=8,
            pady=8,
            width=235,
            highlightbackground=self.formula_theme["line"],
            highlightthickness=0,
            bd=2,
            relief="groove",
        )
        results.pack(side="right", fill="y", padx=(10, 0))
        self.results_sidebar = results
        results.pack_propagate(False)
        result_body = tk.Frame(results, bg=self.formula_theme["panel"])
        result_body.pack(fill="both", expand=True)
        result_scroll = tk.Scrollbar(result_body, orient="vertical")
        result_scroll.pack(side="right", fill="y")
        self.result_canvas = tk.Canvas(
            result_body,
            bg=self.formula_theme["panel"],
            highlightthickness=0,
            yscrollcommand=result_scroll.set,
        )
        self.result_canvas.pack(side="left", fill="both", expand=True)
        result_scroll.configure(command=self.result_canvas.yview)
        self.result_panel = tk.Frame(self.result_canvas, bg=self.formula_theme["panel"])
        self._result_panel_window = self.result_canvas.create_window(
            (0, 0), window=self.result_panel, anchor="nw"
        )
        self.formula_results_table: ttk.Treeview | None = None
        self.result_panel.bind("<Configure>", self.update_result_scrollregion)
        self.result_canvas.bind("<Configure>", self.resize_result_panel)
        self.result_canvas.bind("<MouseWheel>", self.scroll_result_panel)
        tk.Label(
            results,
            text="Ctrl+C — скопировать строки\nиз таблицы результатов.",
            bg=self.formula_theme["panel"],
            fg=MUTED,
            justify="left",
            font="AppSmallFont",
        ).pack(anchor="w", pady=(10, 0))

        status_bar = tk.Frame(self.formula_tab, bg=PAGE, padx=16, pady=6)
        status_bar.pack(side="bottom", fill="x")
        layout.pack(fill="both", expand=True)
        tk.Label(status_bar, text="●", bg=PAGE, fg=MUTED, font="TkDefaultFont").pack(side="left")
        tk.Label(
            status_bar,
            textvariable=self.formula_status,
            bg=PAGE,
            fg=MUTED,
            anchor="w",
            font="TkDefaultFont",
        ).pack(side="left", padx=(6, 0), fill="x", expand=True)

        self._formula_drag = None
        self._formula_drag_origin = "workspace"
        self._wire_drag: dict[str, object] | None = None
        self._wire_handle_drag: dict[str, object] | None = None
        self._formula_pan = False
        self.formula_tool = "pan"
        self.selected_formula_blocks: set[int] = set()
        self._selection_drag: dict[str, float | int] | None = None
        self._selection_rect: int | None = None
        self._frame_drag: dict[str, float | int] | None = None
        self._comment_drag: dict[str, float | int] | None = None
        self.set_formula_tool("pan")
        self.refresh_formula_workspace()

    def configure_formula_styles(self) -> None:
        """Inherit the same table style as the spectrum integral table."""
        ttk.Style(self).configure("Formula.Treeview", font="TkDefaultFont")

    def make_formula_button(
        self, parent: tk.Misc, text: str, command, *, variant: str = "quiet"
    ) -> tk.Button:
        """The same compact desktop buttons as the spectrum tools."""
        return desktop_button(parent, text, command, compact=variant == "icon")

    def focus_formula_scene(self) -> None:
        """Центрировать вид на содержимом схемы без скрытого ограничения масштаба."""
        if not hasattr(self, "formula_canvas"):
            return
        self.formula_canvas.update_idletasks()
        bounds = self.scene_bounds()
        if bounds is None:
            self.formula_canvas.center_at(350, 240)
            self.formula_status.set("Схема пока пуста — добавьте блок из библиотеки слева.")
            return
        left, top, right, bottom = bounds
        self.formula_canvas.center_at((left + right) / 2, (top + bottom) / 2)
        self.formula_status.set("Рабочая область центрирована на схеме.")

    def formula_block_diagnostic(self, block_id: int) -> tuple[str, str]:
        """Use the same diagnostics as the ordinary preset results table."""
        result = self.app.formula_engine_for_record(
            self.spectra.get(self.active_spectrum_id)
        ).evaluate(block_id)
        return result.status, result.message

    def update_formula_health(self) -> None:
        if not hasattr(self, "formula_health"):
            return
        if not self.formula_blocks and not self.formula_comments:
            text, fg = "Новая схема", MUTED
        else:
            diagnostics = [
                self.formula_block_diagnostic(block_id) for block_id in self.formula_blocks
            ]
            errors = sum(state == "error" for state, _message in diagnostics)
            warnings = sum(state == "warning" for state, _message in diagnostics)
            outputs = sum(block.get("kind") == "output" for block in self.formula_blocks.values())
            if errors:
                text, fg = f"{errors} ошибок", ERROR
            elif warnings:
                text, fg = f"{warnings} требуют внимания", WARNING
            elif outputs:
                text, fg = f"Готово · {outputs} вых.", SUCCESS
            else:
                text, fg = "Добавьте вывод", MUTED
        self.formula_health.set(text)
        if hasattr(self, "formula_health_label"):
            self.formula_health_label.configure(bg=PAGE, fg=fg)

    def scroll_formula_palette(self, event) -> str:
        steps = max(1, abs(event.delta) // 120)
        self.palette_canvas.yview_scroll(-steps if event.delta > 0 else steps, "units")
        return "break"

    def on_formula_canvas_configure(self, _event=None) -> None:
        self.draw_formula_grid()
        if (
            self._initial_scene_pending
            and self.formula_canvas.winfo_width() > 1
            and self.formula_canvas.winfo_height() > 1
        ):
            self._initial_scene_pending = False
            self.after_idle(self.focus_formula_scene)
        # До первого <Configure> размер Canvas равен 1×1. Перерисовываем
        # пустое состояние после раскладки, чтобы карточка приветствия не
        # оказалась в левом верхнем углу.
        if (
            hasattr(self, "formula_blocks")
            and not self.formula_blocks
            and not self.formula_comments
        ):
            self.render_formula_blocks()

    def update_result_scrollregion(self, _event=None) -> None:
        if hasattr(self, "result_canvas"):
            self.result_canvas.configure(
                scrollregion=self.result_canvas.bbox("all") or (0, 0, 1, 1)
            )

    def resize_result_panel(self, event) -> None:
        self.result_canvas.itemconfigure(self._result_panel_window, width=event.width)
        self.update_result_scrollregion()

    def scroll_result_panel(self, event) -> str:
        steps = max(1, abs(event.delta) // 120)
        self.result_canvas.yview_scroll(-steps if event.delta > 0 else steps, "units")
        return "break"

    def set_formula_tool(self, tool: str) -> None:
        if tool != "select" and hasattr(self, "selected_formula_blocks"):
            self.clear_formula_selection(render=False)
        self.formula_tool = tool
        labels = {
            "pan": "Режим перемещения поля.",
            "select": "Выделяйте рамкой; выбранные блоки можно переносить вместе или удалить клавишей Delete.",
            "comment": "Щёлкните по доске, чтобы добавить комментарий.",
        }
        for name, button in getattr(self, "formula_tool_buttons", {}).items():
            button.configure(
                relief="sunken" if name == tool else "raised",
                bg=BUTTON_PRESSED if name == tool else BUTTON_SURFACE,
                fg=TEXT,
            )
        if hasattr(self, "formula_status"):
            self.formula_status.set(labels[tool])
        if hasattr(self, "formula_canvas"):
            self.formula_canvas.configure(
                cursor={"pan": "fleur", "select": "crosshair", "comment": "pencil"}[tool]
            )

    def clear_formula_selection(self, *, render: bool = True) -> None:
        """Снять выделение одной командой; новое рамочное выделение его заменяет."""
        self.selected_formula_blocks.clear()
        # Контуры выбранных блоков и временная рамка — это разные объекты
        # Canvas. Удаляем оба типа, иначе после удаления блока оставался
        # «висящий» пунктир.
        self.formula_canvas.delete("selection_outline")
        self.formula_canvas.delete("selection_marquee")
        self._selection_rect = None
        self._selection_drag = None
        if render and hasattr(self, "formula_canvas"):
            self.render_formula_blocks()

    def start_formula_selection(self, event) -> None:
        canvas = self.formula_canvas
        canvas.focus_set()
        current = canvas.find_withtag("current")
        if current and "formula_item" in canvas.gettags(current[0]):
            return
        x, y = float(canvas.canvasx(event.x)), float(canvas.canvasy(event.y))
        if not (event.state & 0x0001):
            # Новая область всегда заменяет старую, даже если она пуста.
            self.clear_formula_selection(render=False)
        if self._selection_rect is not None:
            canvas.delete(self._selection_rect)
        self._selection_rect = canvas.create_rectangle(
            x, y, x, y, outline="#3179c7", width=1, dash=(4, 3), tags="selection_marquee"
        )
        self._selection_drag = {"x": x, "y": y}

    def drag_formula_selection(self, event) -> None:
        if self._selection_drag is None or self._selection_rect is None:
            return
        x, y = (
            float(self.formula_canvas.canvasx(event.x)),
            float(self.formula_canvas.canvasy(event.y)),
        )
        self.formula_canvas.coords(
            self._selection_rect, self._selection_drag["x"], self._selection_drag["y"], x, y
        )

    def finish_formula_selection(self, event) -> None:
        if self._selection_drag is None:
            return
        start_x, start_y = float(self._selection_drag["x"]), float(self._selection_drag["y"])
        end_x, end_y = (
            float(self.formula_canvas.canvasx(event.x)),
            float(self.formula_canvas.canvasy(event.y)),
        )
        left, right = sorted((start_x, end_x))
        top, bottom = sorted((start_y, end_y))
        if abs(right - left) > 3 or abs(bottom - top) > 3:
            for block_id, block in self.formula_blocks.items():
                width, height = self.formula_block_size(block)
                x, y = float(block["x"]), float(block["y"])
                if x < right and x + width > left and y < bottom and y + height > top:
                    self.selected_formula_blocks.add(block_id)
        if self._selection_rect is not None:
            self.formula_canvas.delete(self._selection_rect)
        self._selection_rect = None
        self._selection_drag = None
        self.render_formula_blocks()

    def add_formula_comment_at(self, x: float, y: float) -> None:
        text = simpledialog.askstring("Комментарий", "Текст комментария:", parent=self)
        if not text or not text.strip():
            return
        previous = self.capture_formula_state()
        comment_id = self._next_formula_comment_id
        self._next_formula_comment_id += 1
        self.formula_comments[comment_id] = {"x": x, "y": y, "text": text.strip()}
        self.render_formula_blocks()
        self.remember_formula_state(previous, "добавлен комментарий")

    def start_formula_comment(self, event) -> None:
        self.add_formula_comment_at(
            float(self.formula_canvas.canvasx(event.x)), float(self.formula_canvas.canvasy(event.y))
        )

    def delete_selected_formula_blocks(self, _event=None) -> str | None:
        selected = set(self.selected_formula_blocks) & set(self.formula_blocks)
        if not selected:
            self.formula_status.set("Сначала выделите один или несколько блоков рамкой.")
            return "break" if _event is not None else None
        previous = self.capture_formula_state()
        for block_id in selected:
            self.formula_blocks.pop(block_id, None)
        for block in self.formula_blocks.values():
            if block.get("input") in selected:
                block["input"] = None
            if block.get("inputs"):
                block["inputs"] = [
                    None if value in selected else value for value in block["inputs"]
                ]
        self.selected_formula_blocks.clear()
        self.cleanup_formula_frames()
        self.renumber_formula_outputs()
        self.cleanup_wire_routes()
        self.formula_status.set(f"Удалено блоков: {len(selected)}.")
        self.refresh_formula_workspace()
        self.remember_formula_state(previous, f"удалено блоков: {len(selected)}")
        # Не отдаём событие дальше Tk: это исключает появление меню рабочего
        # поля после нажатия клавиши Delete.
        return "break" if _event is not None else None

    def formula_frame_members(self, frame: dict[str, object]) -> list[int]:
        return [
            int(block_id)
            for block_id in frame.get("members", [])
            if isinstance(block_id, int) and block_id in self.formula_blocks
        ]

    def formula_frame_terminals(self, frame: dict[str, object]) -> list[int]:
        members = self.formula_frame_members(frame)
        member_set = set(members)
        consumed = {
            int(source_id)
            for target_id in member_set
            for source_id in (
                self.formula_blocks[target_id].get("inputs", [])
                if self.formula_blocks[target_id]["kind"] == "operation"
                else []
            )
            if isinstance(source_id, int) and source_id in member_set
        }
        terminals = [
            block_id
            for block_id in members
            if block_id not in consumed and self.formula_blocks[block_id]["kind"] != "output"
        ]
        return terminals or [
            block_id for block_id in members if self.formula_blocks[block_id]["kind"] != "output"
        ]

    def formula_frame_input_targets(self, frame: dict[str, object]) -> list[tuple[int, int]]:
        members = set(self.formula_frame_members(frame))
        targets: list[tuple[int, int]] = []
        for block_id in self.formula_frame_members(frame):
            block = self.formula_blocks[block_id]
            if block["kind"] == "operation":
                targets.extend(
                    (block_id, index)
                    for index, source_id in enumerate(block.get("inputs", []))
                    if source_id not in members
                )
            elif block["kind"] == "output":
                if block.get("input") not in members:
                    targets.append((block_id, 0))
        return targets

    def formula_frame_bounds(self, frame: dict[str, object]) -> tuple[float, float, float, float]:
        members = self.formula_frame_members(frame)
        if not members:
            return float(frame.get("x", 40)), float(frame.get("y", 40)), 170.0, 72.0
        # Оставляем достаточно места у стенок для подписей и проводов портов.
        world_width, _world_height = self._formula_world
        left = max(
            10.0, min(float(self.formula_blocks[block_id]["x"]) for block_id in members) - 96
        )
        top = min(float(self.formula_blocks[block_id]["y"]) for block_id in members) - 34
        right = min(
            world_width - 10.0,
            max(
                float(self.formula_blocks[block_id]["x"])
                + self.formula_block_size(self.formula_blocks[block_id])[0]
                for block_id in members
            )
            + 96,
        )
        bottom = (
            max(
                float(self.formula_blocks[block_id]["y"])
                + self.formula_block_size(self.formula_blocks[block_id])[1]
                for block_id in members
            )
            + 26
        )
        port_rows = max(1, len(frame.get("inputs", [])), len(frame.get("outputs", [])))
        bottom = max(bottom, top + 54.0 + 21.0 * port_rows)
        return left, top, right - left, bottom - top

    def formula_frame_compact_size(self, frame: dict[str, object]) -> tuple[float, float]:
        ports = max(1, len(frame.get("inputs", [])), len(frame.get("outputs", [])))
        return 164.0, max(52.0, 28.0 + ports * 21.0)

    def create_formula_frame(self) -> None:
        members = sorted(set(self.selected_formula_blocks) & set(self.formula_blocks))
        if not members:
            self.formula_status.set("Выделите блоки рамкой, затем создайте из них фрейм.")
            return
        name = simpledialog.askstring(
            "Новый фрейм", "Название фрейма:", initialvalue="Расчёт", parent=self
        )
        if not name or not name.strip():
            return
        previous = self.capture_formula_state()
        frame_id = self._next_formula_frame_id
        self._next_formula_frame_id += 1
        temporary = {"members": members}
        x, y, _width, _height = self.formula_frame_bounds(temporary)
        terminals = self.formula_frame_terminals(temporary)
        input_targets = self.formula_frame_input_targets(temporary)
        self.formula_frames[frame_id] = {
            "name": name.strip(),
            "members": members,
            "collapsed": False,
            "x": x,
            "y": y,
            "inputs": [
                {"name": f"Вход {index + 1}", "target_id": target_id, "index": slot}
                for index, (target_id, slot) in enumerate(input_targets)
            ],
            "outputs": [
                {"name": f"Выход {index + 1}", "source_id": source_id}
                for index, source_id in enumerate(terminals)
            ]
            or [{"name": "Выход 1", "source_id": None}],
        }
        self.selected_formula_blocks.clear()
        self.formula_status.set(
            f"Создан фрейм «{name.strip()}». ПКМ по его заголовку — настройки и сворачивание."
        )
        self.render_formula_blocks()
        self.remember_formula_state(previous, "создан фрейм")

    def configure_formula_frame(self, frame_id: int) -> None:
        frame = self.formula_frames.get(frame_id)
        if frame is None:
            return
        name = simpledialog.askstring(
            "Фрейм", "Название фрейма:", initialvalue=str(frame.get("name", "Фрейм")), parent=self
        )
        if not name or not name.strip():
            return
        input_count = simpledialog.askinteger(
            "Фрейм",
            "Количество входов:",
            initialvalue=len(frame.get("inputs", [])),
            minvalue=0,
            maxvalue=8,
            parent=self,
        )
        if input_count is None:
            return
        output_count = simpledialog.askinteger(
            "Фрейм",
            "Количество выходов:",
            initialvalue=len(frame.get("outputs", [])),
            minvalue=1,
            maxvalue=8,
            parent=self,
        )
        if output_count is None:
            return
        previous = self.capture_formula_state()
        frame["name"] = name.strip()
        targets = self.formula_frame_input_targets(frame)
        if input_count > len(targets):
            messagebox.showinfo(
                "Фрейм",
                f"Внутри этой схемы доступны только {len(targets)} внешних входов. "
                "Количество входов будет ограничено этим числом.",
                parent=self,
            )
            input_count = len(targets)
        old_inputs = list(frame.get("inputs", []))
        frame["inputs"] = []
        for index in range(input_count):
            old = (
                old_inputs[index]
                if index < len(old_inputs) and isinstance(old_inputs[index], dict)
                else {}
            )
            target_id, slot = targets[index % len(targets)] if targets else (None, None)
            port_name = simpledialog.askstring(
                "Вход фрейма",
                f"Название входа {index + 1}:",
                initialvalue=str(old.get("name", f"Вход {index + 1}")),
                parent=self,
            )
            frame["inputs"].append(
                {
                    "name": (port_name or f"Вход {index + 1}").strip(),
                    "target_id": target_id,
                    "index": slot,
                }
            )
        terminals = self.formula_frame_terminals(frame)
        members = set(self.formula_frame_members(frame))
        old_outputs = list(frame.get("outputs", []))
        frame["outputs"] = []
        for index in range(output_count):
            old = (
                old_outputs[index]
                if index < len(old_outputs) and isinstance(old_outputs[index], dict)
                else {}
            )
            source_id = old.get("source_id") if isinstance(old.get("source_id"), int) else None
            if source_id not in members:
                source_id = terminals[index % len(terminals)] if terminals else None
            port_name = simpledialog.askstring(
                "Выход фрейма",
                f"Название выхода {index + 1}:",
                initialvalue=str(old.get("name", f"Выход {index + 1}")),
                parent=self,
            )
            frame["outputs"].append(
                {"name": (port_name or f"Выход {index + 1}").strip(), "source_id": source_id}
            )
        self.cleanup_formula_frames()
        self.render_formula_blocks()
        self.remember_formula_state(previous, "настроен фрейм")

    def toggle_formula_frame(self, frame_id: int) -> None:
        frame = self.formula_frames.get(frame_id)
        if frame is None:
            return
        previous = self.capture_formula_state()
        if not bool(frame.get("collapsed")):
            x, y, _width, _height = self.formula_frame_bounds(frame)
            frame["x"], frame["y"] = x, y
        frame["collapsed"] = not bool(frame.get("collapsed"))
        self.render_formula_blocks()
        self.remember_formula_state(previous, "изменён вид фрейма")

    def ungroup_formula_frame(self, frame_id: int) -> None:
        if frame_id in self.formula_frames:
            previous = self.capture_formula_state()
            self.formula_frames.pop(frame_id)
            self.render_formula_blocks()
            self.remember_formula_state(previous, "разгруппирован фрейм")

    def cleanup_formula_frames(self) -> None:
        for frame_id in list(self.formula_frames):
            frame = self.formula_frames[frame_id]
            frame["members"] = self.formula_frame_members(frame)
            if not frame["members"]:
                self.formula_frames.pop(frame_id)
                continue
            members = set(frame["members"])
            frame["inputs"] = [
                port
                for port in frame.get("inputs", [])
                if isinstance(port, dict) and port.get("target_id") in members
            ]
            frame["outputs"] = [
                port
                for port in frame.get("outputs", [])
                if isinstance(port, dict) and port.get("source_id") in members
            ]
            if not frame["outputs"]:
                terminals = self.formula_frame_terminals(frame)
                frame["outputs"] = [
                    {"name": "Выход 1", "source_id": terminals[-1] if terminals else None}
                ]

    def collapsed_frame_output(self, source_id: int) -> tuple[int, int] | None:
        for frame_id, frame in self.formula_frames.items():
            if bool(frame.get("collapsed")):
                for index, port in enumerate(frame.get("outputs", [])):
                    if isinstance(port, dict) and port.get("source_id") == source_id:
                        return frame_id, index
        return None

    def collapsed_frame_input(self, target_id: int, index: int) -> tuple[int, int] | None:
        for frame_id, frame in self.formula_frames.items():
            if bool(frame.get("collapsed")):
                for port_index, port in enumerate(frame.get("inputs", [])):
                    if (
                        isinstance(port, dict)
                        and port.get("target_id") == target_id
                        and port.get("index") == index
                    ):
                        return frame_id, port_index
        return None

    def frame_output_for_connection(self, source_id: int, target_id: int) -> tuple[int, int] | None:
        """Публичный выход, если провод покидает фрейм."""
        for frame_id, frame in self.formula_frames.items():
            members = set(self.formula_frame_members(frame))
            if source_id not in members or target_id in members:
                continue
            for index, port in enumerate(frame.get("outputs", [])):
                if isinstance(port, dict) and port.get("source_id") == source_id:
                    return frame_id, index
        return None

    def frame_input_for_connection(
        self, source_id: int, target_id: int, index: int
    ) -> tuple[int, int] | None:
        """Публичный вход, если провод приходит в фрейм снаружи."""
        for frame_id, frame in self.formula_frames.items():
            members = set(self.formula_frame_members(frame))
            if target_id not in members or source_id in members:
                continue
            for port_index, port in enumerate(frame.get("inputs", [])):
                if (
                    isinstance(port, dict)
                    and port.get("target_id") == target_id
                    and port.get("index") == index
                ):
                    return frame_id, port_index
        return None

    def hidden_formula_block(self, block_id: int) -> bool:
        return any(
            bool(frame.get("collapsed")) and block_id in self.formula_frame_members(frame)
            for frame in self.formula_frames.values()
        )

    def formula_frame_at(self, x: float, y: float) -> int | None:
        """Найти фрейм по координате холста, даже если ПКМ попало в его фон."""
        for frame_id, frame in reversed(list(self.formula_frames.items())):
            if bool(frame.get("collapsed")):
                left, top = float(frame.get("x", 40.0)), float(frame.get("y", 40.0))
                width, height = self.formula_frame_compact_size(frame)
            else:
                left, top, width, height = self.formula_frame_bounds(frame)
            if left <= x <= left + width and top <= y <= top + height:
                return frame_id
        return None

    def draw_expanded_formula_frames(self) -> None:
        """Открытый фрейм: стенки, порты и видимые связи с содержимым."""
        canvas = self.formula_canvas
        for frame_id, frame in self.formula_frames.items():
            if bool(frame.get("collapsed")):
                continue
            x, y, width, height = self.formula_frame_bounds(frame)
            frame["x"], frame["y"] = x, y
            tags = ("formula_item", f"frame_{frame_id}", f"frame_body_{frame_id}")
            self.rounded_rect(
                canvas,
                x,
                y,
                x + width,
                y + height,
                12,
                fill="#f3f7fd",
                outline="#8aa8c9",
                width=1,
                tags=tags,
            )
            canvas.create_rectangle(x, y, x + width, y + 27, fill="#dfeaf7", outline="", tags=tags)
            canvas.create_text(
                x + 11,
                y + 13.5,
                text=str(frame.get("name") or "Фрейм"),
                anchor="w",
                fill="#294d70",
                font=("Segoe UI", 9, "bold"),
                tags=tags,
            )
            canvas.create_text(
                x + width - 9,
                y + 13.5,
                text="ПКМ",
                anchor="e",
                fill="#64809c",
                font=("Segoe UI", 7),
                tags=tags,
            )
            self.draw_frame_internal_connections(frame_id, frame)
            for index, port in enumerate(frame.get("inputs", [])):
                if not isinstance(port, dict):
                    continue
                port_x, port_y = self.formula_frame_port_position(frame_id, "in", index)
                canvas.create_text(
                    port_x + 12,
                    port_y,
                    text=str(port.get("name") or f"Вход {index + 1}"),
                    anchor="w",
                    fill="#3b6487",
                    font=("Segoe UI", 8),
                    width=68,
                    tags=tags,
                )
                target_id, target_index = port.get("target_id"), port.get("index")
                self.draw_formula_frame_port(
                    frame_id,
                    "in",
                    index,
                    int(target_id) if isinstance(target_id, int) else None,
                    int(target_index) if isinstance(target_index, int) else None,
                )
            for index, port in enumerate(frame.get("outputs", [])):
                if not isinstance(port, dict):
                    continue
                port_x, port_y = self.formula_frame_port_position(frame_id, "out", index)
                canvas.create_text(
                    port_x - 12,
                    port_y,
                    text=str(port.get("name") or f"Выход {index + 1}"),
                    anchor="e",
                    fill="#8a5a18",
                    font=("Segoe UI", 8),
                    width=68,
                    tags=tags,
                )
                source_id = port.get("source_id")
                self.draw_formula_frame_port(
                    frame_id, "out", index, int(source_id) if isinstance(source_id, int) else None
                )
            for sequence, callback in (
                (
                    "<ButtonPress-1>",
                    lambda event, fid=frame_id: self.start_formula_frame_drag(event, fid),
                ),
                ("<B1-Motion>", self.drag_formula_frame),
                ("<ButtonRelease-1>", self.drop_formula_frame),
            ):
                canvas.tag_bind(f"frame_body_{frame_id}", sequence, callback)

    def draw_frame_internal_connections(self, frame_id: int, frame: dict[str, object]) -> None:
        """Показать, к каким внутренним блокам привязаны порты стенок."""
        canvas = self.formula_canvas
        tags = ("formula_item", f"frame_{frame_id}", "frame_internal")
        for index, port in enumerate(frame.get("inputs", [])):
            if not isinstance(port, dict):
                continue
            target_id, target_index = port.get("target_id"), port.get("index")
            if (
                not isinstance(target_id, int)
                or not isinstance(target_index, int)
                or target_id not in self.formula_blocks
            ):
                continue
            start_x, start_y = self.formula_frame_port_position(frame_id, "in", index)
            end_x, end_y = self.formula_port_position(target_id, "in", target_index)
            middle_x = (start_x + end_x) / 2
            points = (
                self.automatic_wire_points((start_x, start_y), (end_x, end_y))
                if self.document.auto_route
                else [start_x, start_y, middle_x, start_y, middle_x, end_y, end_x, end_y]
            )
            canvas.create_line(
                *points,
                fill="#5d94c3",
                width=1.5,
                dash=(3, 2),
                joinstyle="round",
                tags=tags,
            )
        for index, port in enumerate(frame.get("outputs", [])):
            if not isinstance(port, dict):
                continue
            source_id = port.get("source_id")
            if not isinstance(source_id, int) or source_id not in self.formula_blocks:
                continue
            start_x, start_y = self.formula_port_position(source_id, "out")
            end_x, end_y = self.formula_frame_port_position(frame_id, "out", index)
            middle_x = (start_x + end_x) / 2
            points = (
                self.automatic_wire_points((start_x, start_y), (end_x, end_y))
                if self.document.auto_route
                else [start_x, start_y, middle_x, start_y, middle_x, end_y, end_x, end_y]
            )
            canvas.create_line(
                *points,
                fill="#d49336",
                width=1.5,
                dash=(3, 2),
                joinstyle="round",
                tags=tags,
            )

    def draw_collapsed_formula_frames(self) -> None:
        """Компактные операторы: только имя и настраиваемые входы/выходы."""
        canvas = self.formula_canvas
        for frame_id, frame in self.formula_frames.items():
            if not bool(frame.get("collapsed")):
                continue
            x, y = float(frame.get("x", 40.0)), float(frame.get("y", 40.0))
            width, height = self.formula_frame_compact_size(frame)
            tags = ("formula_item", f"frame_{frame_id}", f"frame_body_{frame_id}")
            self.rounded_rect(
                canvas,
                x,
                y,
                x + width,
                y + height,
                12,
                fill="#386f9c",
                outline="#245271",
                width=1,
                tags=tags,
            )
            canvas.create_rectangle(
                x + 1, y + 1, x + width - 1, y + 26, fill="#4d86b3", outline="", tags=tags
            )
            canvas.create_text(
                x + 10,
                y + 13.5,
                text=str(frame.get("name") or "Фрейм"),
                anchor="w",
                fill="white",
                font=("Segoe UI", 9, "bold"),
                width=132,
                tags=tags,
            )
            inputs = [port for port in frame.get("inputs", []) if isinstance(port, dict)]
            outputs = [port for port in frame.get("outputs", []) if isinstance(port, dict)]
            rows = max(len(inputs), len(outputs), 1)
            for index in range(rows):
                port_y = y + 34.0 + index * 21.0
                if index < len(inputs):
                    canvas.create_text(
                        x + 11,
                        port_y,
                        text=str(inputs[index].get("name") or f"Вход {index + 1}"),
                        anchor="w",
                        fill="#eaf4ff",
                        font=("Segoe UI", 8),
                        width=60,
                        tags=tags,
                    )
                    target_id = inputs[index].get("target_id")
                    target_slot = inputs[index].get("index")
                    self.draw_formula_frame_port(
                        frame_id,
                        "in",
                        index,
                        int(target_id) if isinstance(target_id, int) else None,
                        int(target_slot) if isinstance(target_slot, int) else None,
                    )
                if index < len(outputs):
                    canvas.create_text(
                        x + width - 11,
                        port_y,
                        text=str(outputs[index].get("name") or f"Выход {index + 1}"),
                        anchor="e",
                        fill="#fff2d5",
                        font=("Segoe UI", 8),
                        width=65,
                        tags=tags,
                    )
                    source_id = outputs[index].get("source_id")
                    self.draw_formula_frame_port(
                        frame_id,
                        "out",
                        index,
                        int(source_id) if isinstance(source_id, int) else None,
                    )
            for sequence, callback in (
                (
                    "<ButtonPress-1>",
                    lambda event, fid=frame_id: self.start_formula_frame_drag(event, fid),
                ),
                ("<B1-Motion>", self.drag_formula_frame),
                ("<ButtonRelease-1>", self.drop_formula_frame),
            ):
                canvas.tag_bind(f"frame_body_{frame_id}", sequence, callback)

    def start_formula_frame_drag(self, event, frame_id: int) -> str:
        if self.formula_tool == "comment":
            self.add_formula_comment_at(
                float(self.formula_canvas.canvasx(event.x)),
                float(self.formula_canvas.canvasy(event.y)),
            )
            return "break"
        if frame_id not in self.formula_frames:
            return "break"
        x, y = self.formula_canvas.canvasx(event.x), self.formula_canvas.canvasy(event.y)
        self._formula_drag = None
        self._frame_drag = {
            "id": frame_id,
            "x": float(x),
            "y": float(y),
            "history": self.capture_formula_state(),
        }
        return "break"

    def drag_formula_frame(self, event) -> str | None:
        if self._frame_drag is None:
            return None
        frame_id = int(self._frame_drag["id"])
        frame = self.formula_frames.get(frame_id)
        if frame is None:
            return "break"
        x, y = (
            float(self.formula_canvas.canvasx(event.x)),
            float(self.formula_canvas.canvasy(event.y)),
        )
        previous_x, previous_y = float(self._frame_drag["x"]), float(self._frame_drag["y"])
        dx, dy = x - previous_x, y - previous_y
        left, top, width, height = self.formula_frame_bounds(frame)
        world_width, world_height = self._formula_world
        dx = min(max(dx, 10.0 - left), world_width - 10.0 - (left + width))
        dy = min(max(dy, 10.0 - top), world_height - 10.0 - (top + height))
        for block_id in self.formula_frame_members(frame):
            block = self.formula_blocks[block_id]
            block["x"] = float(block["x"]) + dx
            block["y"] = float(block["y"]) + dy
            if not bool(frame.get("collapsed")):
                self.formula_canvas.move(f"block_{block_id}", dx, dy)
        frame["x"], frame["y"] = float(frame.get("x", left)) + dx, float(frame.get("y", top)) + dy
        self.formula_canvas.move(f"frame_{frame_id}", dx, dy)
        self._frame_drag["x"], self._frame_drag["y"] = x, y
        return "break"

    def drop_formula_frame(self, _event) -> str | None:
        if self._frame_drag is None:
            return None
        previous = self._frame_drag.get("history")
        self._frame_drag = None
        self.render_formula_blocks()
        if isinstance(previous, dict):
            self.remember_formula_state(previous, "перемещён фрейм")
        return "break"

    def formula_frame_menu(self, event, frame_id: int) -> str:
        frame = self.formula_frames.get(frame_id)
        if frame is None:
            return "break"
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(
            label="Открыть" if bool(frame.get("collapsed")) else "Сжать",
            command=lambda: self.toggle_formula_frame(frame_id),
        )
        menu.add_command(
            label="Настроить фрейм…", command=lambda: self.configure_formula_frame(frame_id)
        )
        menu.add_separator()
        menu.add_command(
            label="Удалить фрейм (оставить содержимое)",
            command=lambda: self.ungroup_formula_frame(frame_id),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def start_formula_pan(self, event) -> None:
        """ЛКМ по пустому фону перемещает вид рабочей области."""
        if self.formula_tool == "select":
            self.start_formula_selection(event)
            return
        if self.formula_tool == "comment":
            self.start_formula_comment(event)
            return
        current = self.formula_canvas.find_withtag("current")
        if current and "formula_item" in self.formula_canvas.gettags(current[0]):
            self._formula_pan = False
            return
        self._formula_pan = True
        self.formula_canvas.scan_mark(event.x, event.y)

    def drag_formula_pan(self, event) -> None:
        if self.formula_tool == "select":
            self.drag_formula_selection(event)
            return
        if self._formula_pan:
            self.formula_canvas.scan_dragto(event.x, event.y, gain=1)

    def stop_formula_pan(self, event) -> None:
        if self.formula_tool == "select":
            self.finish_formula_selection(event)
        self._formula_pan = False

    def draw_formula_grid(self) -> None:
        if not hasattr(self, "formula_canvas"):
            return
        canvas = self.formula_canvas
        canvas.delete("formula_grid")
        max_x = max(
            (float(b["x"]) + self.formula_block_size(b)[0] for b in self.formula_blocks.values()),
            default=0,
        )
        max_y = max(
            (float(b["y"]) + self.formula_block_size(b)[1] for b in self.formula_blocks.values()),
            default=0,
        )
        max_x = max(
            max_x, max((float(c["x"]) + 230 for c in self.formula_comments.values()), default=0)
        )
        max_y = max(
            max_y, max((float(c["y"]) + 90 for c in self.formula_comments.values()), default=0)
        )
        for frame in self.formula_frames.values():
            if frame.get("collapsed"):
                w, h = self.formula_frame_compact_size(frame)
                max_x, max_y = max(max_x, float(frame["x"]) + w), max(max_y, float(frame["y"]) + h)
        width = int(max(2000, max_x + 700, canvas.winfo_width() / canvas.zoom + 500))
        height = int(max(1400, max_y + 500, canvas.winfo_height() / canvas.zoom + 400))
        self._formula_world = (width, height)
        canvas.configure(scrollregion=(0, 0, width, height))
        for x in range(0, width + 1, 24):
            canvas.create_line(x, 0, x, height, fill=GRID, tags="formula_grid")
        for y in range(0, height + 1, 24):
            canvas.create_line(0, y, width, y, fill=GRID, tags="formula_grid")
        canvas.tag_lower("formula_grid")

    def refresh_formula_workspace(self) -> None:
        if not hasattr(self, "formula_canvas"):
            return
        self.formula_canvas.zoom = self.document.zoom
        self.zoom_label.set(f"{self.document.zoom:.0%}")
        self.draw_formula_grid()
        self.render_formula_palette()
        self.render_formula_blocks()

    def render_formula_palette(self) -> None:
        canvas = self.palette_canvas
        canvas.delete("all")
        theme = self.formula_theme
        y = 14
        canvas.create_text(
            10, y, text="ИНТЕГРАЛЫ", anchor="w", fill="#617086", font=("Segoe UI", 8, "bold")
        )
        canvas.create_text(
            10,
            y + 15,
            text="Нажмите для добавления",
            anchor="w",
            fill="#9aa7b8",
            font=("Segoe UI", 8),
        )
        y += 29
        comparison = self.comparison_record()
        source_integrals = (
            sorted(list(comparison.get("integrals", [])), key=lambda item: int(item["number"]))
            if comparison is not None
            else self.ordered_integrals()
        )
        for item in source_integrals:
            number = (
                str(item["number"]) if comparison is not None else self.integral_number(item["id"])
            )
            integral_id, color = int(item["id"]), str(item["color"])
            tag = f"palette_area_{integral_id}"
            self.rounded_rect(
                canvas,
                10,
                y + 2,
                187,
                y + 36,
                12,
                fill="#dce4ef",
                outline="",
                tags=("palette_shadow",),
            )
            self.rounded_rect(canvas, 8, y, 185, y + 34, 12, fill=color, outline="", tags=(tag,))
            canvas.create_text(
                20,
                y + 17,
                text=f"I{number}",
                anchor="w",
                fill="white",
                font=("Segoe UI", 10, "bold"),
                tags=(tag,),
            )
            if comparison is not None and self.active_spectrum_id in self.spectra:
                record = self.spectra[self.active_spectrum_id]
                offsets = comparison.get("offsets", {})
                offset = float(offsets.get(record["id"], offsets.get(str(record["id"]), 0.0)))
                value = self.record_integral_area(
                    record, float(item["left"]), float(item["right"]), x_offset=offset
                )
            else:
                value = self.integral_area(item)
            canvas.create_text(
                172,
                y + 17,
                text=self.format_formula_value(value),
                anchor="e",
                fill="white",
                font=("Consolas", 9, "bold"),
                tags=(tag,),
            )
            canvas.tag_bind(
                tag,
                "<ButtonPress-1>",
                lambda event, iid=integral_id, num=int(number): self.start_palette_drag(
                    event, iid, num
                ),
            )
            y += 41
        if not source_integrals:
            canvas.create_text(
                10, y + 8, text="Сначала выделите интеграл", anchor="w", fill="#98a1aa"
            )
            y += 30
        tag = "palette_numbered_area"
        self.rounded_rect(
            canvas, 8, y, 185, y + 28, 9, fill="#edf4ff", outline="#d8e6fc", tags=(tag,)
        )
        canvas.create_text(
            18,
            y + 14,
            text="＋  Источник I…",
            anchor="w",
            fill=theme["primary"],
            font=("Segoe UI", 9, "bold"),
            tags=(tag,),
        )
        canvas.tag_bind(tag, "<ButtonPress-1>", lambda _event: self.add_area_source())
        y += 36
        canvas.create_line(10, y, 185, y, fill=theme["line"])
        y += 17
        canvas.create_text(
            10, y, text="КОНСТАНТЫ", anchor="w", fill="#617086", font=("Segoe UI", 8, "bold")
        )
        y += 17
        action_y = y + 5
        self.rounded_rect(
            canvas,
            8,
            action_y,
            185,
            action_y + 30,
            10,
            fill="#f1edff",
            outline="#ddd5ff",
            tags=("palette_action", "add_constant"),
        )
        canvas.create_text(
            18,
            action_y + 15,
            text="＋  Константа",
            anchor="w",
            fill=theme["violet"],
            font=("Segoe UI", 10, "bold"),
            tags=("palette_action", "add_constant"),
        )
        canvas.tag_bind(
            "add_constant", "<ButtonPress-1>", lambda _event: self.add_formula_constant()
        )
        y = action_y + 35
        canvas.create_line(10, y, 185, y, fill=theme["line"])
        y += 17
        canvas.create_text(
            10, y, text="ОПЕРАЦИИ", anchor="w", fill="#617086", font=("Segoe UI", 8, "bold")
        )
        y += 17
        for row in (
            ("+", "−"),
            ("×", "÷"),
            ("^", "%"),
            ("abs", "√"),
            ("ln", "log₁₀"),
            ("exp", "round"),
            ("min", "max"),
        ):
            for column, operation in enumerate(row):
                left = 8 + column * 91
                tag = f"palette_operation_{operation}_{y}"
                self.draw_palette_operation(canvas, left, y, left + 84, y + 32, operation, tag)
                canvas.tag_bind(
                    tag,
                    "<ButtonPress-1>",
                    lambda _event, op=operation: self.add_formula_operation_from_palette(op),
                )
            y += 39
        output_tag = "palette_output"
        self.rounded_rect(
            canvas, 10, y + 2, 187, y + 34, 12, fill="#dce4ef", outline="", tags=("palette_shadow",)
        )
        self.rounded_rect(
            canvas, 8, y, 185, y + 32, 12, fill="#3576c0", outline="", tags=(output_tag,)
        )
        canvas.create_text(
            20,
            y + 16,
            text="＋  Результат",
            anchor="w",
            fill="white",
            font=("Segoe UI", 9, "bold"),
            tags=(output_tag,),
        )
        canvas.tag_bind(
            output_tag, "<ButtonPress-1>", lambda _event: self.add_formula_output_from_palette()
        )
        y += 40
        canvas.create_text(
            10,
            y + 2,
            text="ПКМ на поле — все команды",
            anchor="w",
            fill="#98a1aa",
            font=("Segoe UI", 8),
        )
        canvas.configure(scrollregion=(0, 0, 195, y + 25))

    def draw_palette_operation(
        self, canvas, x1: float, y1: float, x2: float, y2: float, operation: str, tag: str
    ) -> None:
        """Короткая, но читаемая карточка операции в библиотеке."""
        self.rounded_rect(
            canvas,
            x1 + 2,
            y1 + 2,
            x2 + 2,
            y2 + 2,
            13,
            fill="#d8e7df",
            outline="",
            tags=("palette_shadow",),
        )
        self.rounded_rect(
            canvas, x1, y1, x2, y2, 13, fill="#eaf8f0", outline="#b7e1c7", tags=(tag,)
        )
        if operation in self._UNARY_OPERATIONS:
            self.rounded_rect(
                canvas,
                x1 + 7,
                y1 + 7,
                x1 + 31,
                y2 - 7,
                10,
                fill="#ffffff",
                outline="#76bb93",
                tags=(tag,),
            )
            canvas.create_text(
                x1 + 56,
                (y1 + y2) / 2,
                text=operation,
                fill="#24744c",
                font=("Segoe UI", 10, "bold"),
                tags=(tag,),
            )
        else:
            self.rounded_rect(
                canvas,
                x1 + 6,
                y1 + 7,
                x1 + 28,
                y2 - 7,
                9,
                fill="#ffffff",
                outline="#76bb93",
                tags=(tag,),
            )
            self.rounded_rect(
                canvas,
                x2 - 28,
                y1 + 7,
                x2 - 6,
                y2 - 7,
                9,
                fill="#ffffff",
                outline="#76bb93",
                tags=(tag,),
            )
            canvas.create_text(
                (x1 + x2) / 2,
                (y1 + y2) / 2,
                text=operation,
                fill="#24744c",
                font=("Segoe UI", 11, "bold"),
                tags=(tag,),
            )

    @staticmethod
    def rounded_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
        points = [
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        ]
        return canvas.create_polygon(points, smooth=True, splinesteps=12, **kwargs)

    def draw_formula_shadow(
        self, x: float, y: float, width: float, height: float, radius: float = 12
    ) -> None:
        """Лёгкая тень отделяет блок от сетки, не перехватывая клики мыши."""
        self.rounded_rect(
            self.formula_canvas,
            x + 2,
            y + 3,
            x + width + 2,
            y + height + 3,
            radius,
            fill="#d7e0eb",
            outline="",
            tags=("formula_shadow",),
        )

    def formula_input_is_connected(self, block_id: int, index: int | None) -> bool:
        block = self.formula_blocks.get(block_id)
        if block is None:
            return False
        if block.get("kind") == "operation":
            inputs = list(block.get("inputs", []))
            value = inputs[index] if isinstance(index, int) and 0 <= index < len(inputs) else None
        else:
            value = block.get("input")
        return isinstance(value, int) and value in self.formula_blocks

    def add_formula_block(
        self,
        kind: str,
        x: float,
        y: float,
        *,
        integral_id: int | None = None,
        operation: str | None = None,
        value: float | None = None,
        name: str | None = None,
        area_number: int | None = None,
    ) -> int:
        previous = self.capture_formula_state()
        block_id = self._next_formula_id
        self._next_formula_id += 1
        unary = operation in self._UNARY_OPERATIONS
        width, height = self.formula_block_size({"kind": kind})
        world_width, world_height = getattr(self, "_formula_world", (1800.0, 1200.0))
        x = min(max(10.0, float(x)), world_width - width - 10.0)
        y = min(max(10.0, float(y)), world_height - height - 10.0)
        output_number = 1 + sum(
            1 for item in self.formula_blocks.values() if item["kind"] == "output"
        )
        auto_name = kind == "output" and name is None
        output_name = "Вывод" if output_number == 1 else f"Вывод {output_number}"
        self.formula_blocks[block_id] = {
            "kind": kind,
            "x": x,
            "y": y,
            "integral_id": integral_id,
            "area_number": area_number
            if area_number is not None
            else (
                int(self.integral_number(integral_id))
                if kind == "area" and self.integral_number(integral_id).isdigit()
                else None
            ),
            "comparison_id": self.current_comparison_id if kind == "area" else None,
            "operation": operation,
            "inputs": ([None] if unary else [None, None]) if kind == "operation" else [],
            "input": None,
            "value": value,
            "name": output_name if auto_name else (name or ""),
            "auto_name": auto_name,
        }
        self.render_formula_blocks()
        labels = {
            "area": "добавлена площадь",
            "constant": "добавлена константа",
            "operation": "добавлена операция",
            "output": "добавлен результат",
        }
        self.remember_formula_state(previous, labels.get(kind, "добавлен блок"))
        return block_id

    def formula_spawn_point(self) -> tuple[float, float]:
        """Свободное место в текущем видимом участке рабочей области."""
        canvas = self.formula_canvas
        index = len(self.formula_blocks)
        left = float(canvas.canvasx(45)) + (index % 4) * 34
        top = float(canvas.canvasy(55)) + (index % 5) * 30
        return left, top

    def start_palette_drag(self, _event, integral_id: int, area_number: int | None = None) -> None:
        x, y = self.formula_spawn_point()
        self.add_formula_block("area", x, y, integral_id=integral_id, area_number=area_number)

    def add_area_source(self):
        number = simpledialog.askinteger(
            "Источник площади", "Номер интеграла I:", minvalue=1, parent=self
        )
        if number is None:
            return
        item = next((i for i in self.app.formula_source_integrals() if i["number"] == number), None)
        x, y = self.formula_spawn_point()
        self.add_formula_block(
            "area", x, y, integral_id=item["id"] if item else None, area_number=number
        )

    def add_formula_operation_from_palette(self, operation: str) -> None:
        x, y = self.formula_spawn_point()
        self.add_formula_block("operation", x, y, operation=operation)

    def add_formula_output_from_palette(self) -> None:
        x, y = self.formula_spawn_point()
        self.add_formula_block("output", x, y)

    def ask_formula_number(
        self, title: str, prompt: str, initial: float | None = None
    ) -> float | None:
        """Числовой ввод, принимающий и запятую, и точку."""
        initial_text = None if initial is None else f"{initial:.12g}"
        entered = simpledialog.askstring(title, prompt, initialvalue=initial_text, parent=self)
        if entered is None:
            return None
        try:
            return float(entered.strip().replace(",", "."))
        except ValueError:
            messagebox.showerror(title, "Введите корректное число.", parent=self)
            return None

    def add_formula_constant(self, x: float | None = None, y: float | None = None) -> None:
        value = self.ask_formula_number("Константа", "Введите численное значение:")
        if value is not None:
            if x is None or y is None:
                x, y = self.formula_spawn_point()
            self.add_formula_block("constant", x, y, value=value)

    def render_formula_blocks(self) -> None:
        self.app.invalidate_formula_results()
        self.draw_formula_grid()
        if self.document.auto_route:
            signature = repr((self.formula_blocks, self.formula_frames, self.formula_comments))
            if signature != getattr(self, "_routing_signature", None):
                self._routing_signature = signature
                self._automatic_paths = {}
                self._occupied_wire_segments = []
        canvas = self.formula_canvas
        canvas.delete("formula_item")
        canvas.delete("formula_shadow")
        canvas.delete("formula_empty")
        canvas.delete("wire_preview")
        canvas.delete("selection_outline")
        # Если перерисовка произошла во время смены инструмента, временная
        # рамка также не должна превращаться в постоянный пунктир.
        if self._selection_drag is None:
            canvas.delete("selection_marquee")
        if not self.formula_blocks and not self.formula_comments:
            center_x = float(canvas.canvasx(max(1, canvas.winfo_width()) / 2))
            center_y = float(canvas.canvasy(max(1, canvas.winfo_height()) / 2))
            left, top = center_x - 178, center_y - 74
            self.rounded_rect(
                canvas,
                left + 3,
                top + 4,
                left + 363,
                top + 142,
                18,
                fill="#dbe5f0",
                outline="",
                tags=("formula_empty",),
            )
            self.rounded_rect(
                canvas,
                left,
                top,
                left + 360,
                top + 138,
                18,
                fill="#ffffff",
                outline="#dbe5f0",
                tags=("formula_empty",),
            )
            canvas.create_text(
                center_x,
                top + 37,
                text="Соберите первый расчёт",
                fill="#24344d",
                font=("Segoe UI", 13, "bold"),
                tags=("formula_empty",),
            )
            canvas.create_text(
                center_x,
                top + 69,
                text="1. Добавьте интеграл   2. Соедините операцию   3. Добавьте результат",
                fill="#718096",
                font=("Segoe UI", 9),
                tags=("formula_empty",),
            )
            canvas.create_text(
                center_x,
                top + 102,
                text="V — выделение   C — комментарий   Ctrl+Z — отмена",
                fill="#2e6ac1",
                font=("Segoe UI", 8, "bold"),
                tags=("formula_empty",),
            )
            self.update_formula_health()
            self.refresh_result_panel()
            self.app.refresh_calculations()
            return
        # Развёрнутый фрейм рисуется первым: его рамка остаётся под блоками и
        # не перекрывает ни сами блоки, ни соединения.
        self.draw_expanded_formula_frames()
        self.draw_formula_connections()
        for block_id, block in self.formula_blocks.items():
            if self.hidden_formula_block(block_id):
                continue
            kind, x, y = str(block["kind"]), float(block["x"]), float(block["y"])
            body_tag = ("formula_item", f"block_{block_id}", f"node_body_{block_id}")
            if kind in {"area", "constant"}:
                item = (
                    next(
                        (
                            value
                            for value in self.app.formula_source_integrals()
                            if str(value.get("number")) == self.formula_area_number(block)
                        ),
                        None,
                    )
                    if kind == "area"
                    else None
                )
                color = (
                    str(item["color"])
                    if kind == "area" and item
                    else "#7b61ff"
                    if kind == "constant"
                    else "#9aa5b1"
                )
                if kind == "area":
                    width, height = self.formula_block_size(block)
                    self.draw_formula_shadow(x, y, width, height, 10)
                    self.rounded_rect(
                        canvas,
                        x,
                        y,
                        x + width,
                        y + height,
                        10,
                        fill=color,
                        outline="#ffffff",
                        width=1,
                        tags=body_tag,
                    )
                    canvas.create_text(
                        x + width / 2,
                        y + height / 2,
                        text=self.formula_area_number(block),
                        fill="white",
                        font=("Segoe UI", 14, "bold"),
                        tags=body_tag,
                    )
                else:
                    width, height = self.formula_block_size(block)
                    self.draw_formula_shadow(x, y, width, height, 10)
                    self.rounded_rect(
                        canvas,
                        x,
                        y,
                        x + width,
                        y + height,
                        10,
                        fill=color,
                        outline="#ffffff",
                        width=1,
                        tags=body_tag,
                    )
                    canvas.create_text(
                        x + width / 2,
                        y + height / 2,
                        text=self.format_formula_value(self.formula_value(block_id)),
                        fill="white",
                        font=("Consolas", 10, "bold"),
                        tags=body_tag,
                    )
                self.draw_formula_port(block_id, "out", None)
            elif kind == "operation":
                operation = str(block["operation"])
                state, _message = self.formula_block_diagnostic(block_id)
                outline = (
                    "#f1a21a"
                    if state == "warning"
                    else "#d94b63"
                    if state == "error"
                    else "#197a4d"
                )
                fill = "#3ca36c" if state != "error" else "#c86475"
                self.draw_formula_shadow(x, y, 88, 64, 16)
                self.rounded_rect(
                    canvas,
                    x,
                    y,
                    x + 88,
                    y + 64,
                    16,
                    fill=fill,
                    outline=outline,
                    width=2,
                    tags=body_tag,
                )
                canvas.create_text(
                    x + 44,
                    y + 32,
                    text=operation,
                    fill="white",
                    font=("Segoe UI", 20, "bold"),
                    tags=body_tag,
                )
                for index in range(len(block["inputs"])):
                    self.draw_formula_port(block_id, "in", index)
                self.draw_formula_port(block_id, "out", None)
            else:
                width, height = self.formula_block_size(block)
                state, _message = self.formula_block_diagnostic(block_id)
                outline = (
                    "#f1a21a"
                    if state == "warning"
                    else "#d94b63"
                    if state == "error"
                    else "#205b9c"
                )
                fill = "#3b7dc5" if state != "error" else "#c86475"
                self.draw_formula_shadow(x, y, width, height, 15)
                self.rounded_rect(
                    canvas,
                    x,
                    y,
                    x + width,
                    y + height,
                    15,
                    fill=fill,
                    outline=outline,
                    width=2,
                    tags=body_tag,
                )
                canvas.create_text(
                    x + width / 2,
                    y + height / 2,
                    text=str(block.get("name") or "Вывод"),
                    fill="white",
                    font=("Segoe UI", 9, "bold"),
                    tags=body_tag,
                )
                self.draw_formula_port(block_id, "in", 0)
            for sequence, callback in (
                (
                    "<ButtonPress-1>",
                    lambda event, bid=block_id: self.start_formula_drag(event, bid),
                ),
                ("<B1-Motion>", self.drag_formula_block),
                ("<ButtonRelease-1>", self.drop_formula_block),
            ):
                canvas.tag_bind(f"node_body_{block_id}", sequence, callback)
            if kind == "constant":
                canvas.tag_bind(
                    f"node_body_{block_id}",
                    "<Double-Button-1>",
                    lambda _event, bid=block_id: self.edit_formula_constant(bid),
                )
            if block_id in self.selected_formula_blocks:
                width, height = self.formula_block_size(block)
                canvas.create_rectangle(
                    x - 4,
                    y - 4,
                    x + width + 4,
                    y + height + 4,
                    outline="#2563eb",
                    width=2,
                    tags="selection_outline",
                )
        # Свёрнутый фрейм показывается поверх проводов, чтобы его порты были
        # легко различимы и за них можно было тянуть соединения.
        self.draw_collapsed_formula_frames()
        self.render_formula_comments()
        self.update_formula_health()
        self.refresh_result_panel()
        self.app.refresh_calculations()

    def render_formula_comments(self) -> None:
        """Лёгкие заметки на доске; они не участвуют в вычислениях."""
        canvas = self.formula_canvas
        for comment_id, comment in self.formula_comments.items():
            x, y = float(comment["x"]), float(comment["y"])
            tag = ("formula_item", "formula_comment", f"comment_{comment_id}")
            self.rounded_rect(
                canvas, x, y, x + 170, y + 50, 9, fill="#fff4b8", outline="#d8bd56", tags=tag
            )
            canvas.create_text(
                x + 10,
                y + 9,
                text=str(comment["text"]),
                anchor="nw",
                width=150,
                fill="#5c4a0d",
                font=("Segoe UI", 9),
                tags=tag,
            )
            canvas.tag_bind(
                f"comment_{comment_id}",
                "<ButtonPress-1>",
                lambda event, cid=comment_id: self.start_formula_comment_drag(event, cid),
            )
            canvas.tag_bind(f"comment_{comment_id}", "<B1-Motion>", self.drag_formula_comment)
            canvas.tag_bind(f"comment_{comment_id}", "<ButtonRelease-1>", self.drop_formula_comment)
            canvas.tag_bind(
                f"comment_{comment_id}",
                "<Button-3>",
                lambda event, cid=comment_id: self.formula_comment_menu(event, cid),
            )

    def start_formula_comment_drag(self, event, comment_id: int) -> str | None:
        if self.formula_tool == "comment":
            return "break"
        x, y = self.formula_canvas.canvasx(event.x), self.formula_canvas.canvasy(event.y)
        self._comment_drag = {
            "id": comment_id,
            "x": float(x),
            "y": float(y),
            "history": self.capture_formula_state(),
        }
        return "break"

    def drag_formula_comment(self, event) -> str | None:
        if self._comment_drag is None:
            return None
        comment_id = int(self._comment_drag["id"])
        x, y = (
            float(self.formula_canvas.canvasx(event.x)),
            float(self.formula_canvas.canvasy(event.y)),
        )
        comment = self.formula_comments[comment_id]
        dx, dy = x - float(self._comment_drag["x"]), y - float(self._comment_drag["y"])
        comment["x"], comment["y"] = float(comment["x"]) + dx, float(comment["y"]) + dy
        self._comment_drag["x"], self._comment_drag["y"] = x, y
        self.formula_canvas.move(f"comment_{comment_id}", dx, dy)
        return "break"

    def drop_formula_comment(self, _event) -> str | None:
        if self._comment_drag is None:
            return None
        previous = self._comment_drag.get("history")
        self._comment_drag = None
        if self.document.auto_route:
            self.render_formula_blocks()
        if isinstance(previous, dict):
            self.remember_formula_state(previous, "перемещён комментарий")
        return "break"

    def formula_comment_menu(self, event, comment_id: int) -> str:
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(
            label="Изменить комментарий…", command=lambda: self.edit_formula_comment(comment_id)
        )
        menu.add_command(
            label="Удалить комментарий", command=lambda: self.delete_formula_comment(comment_id)
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def edit_formula_comment(self, comment_id: int) -> None:
        comment = self.formula_comments.get(comment_id)
        if comment is None:
            return
        text = simpledialog.askstring(
            "Комментарий", "Текст комментария:", initialvalue=str(comment["text"]), parent=self
        )
        if text and text.strip():
            previous = self.capture_formula_state()
            comment["text"] = text.strip()
            self.render_formula_blocks()
            self.remember_formula_state(previous, "изменён комментарий")

    def delete_formula_comment(self, comment_id: int) -> None:
        if comment_id in self.formula_comments:
            previous = self.capture_formula_state()
            self.formula_comments.pop(comment_id, None)
            self.render_formula_blocks()
            self.remember_formula_state(previous, "удалён комментарий")

    @staticmethod
    def formula_block_size(block: dict[str, object]) -> tuple[float, float]:
        return block_size(block)

    def formula_slot_rects(
        self, block: dict[str, object]
    ) -> list[tuple[float, float, float, float]]:
        x, y = float(block["x"]), float(block["y"])
        if block["kind"] == "operation":
            if block["operation"] in self._UNARY_OPERATIONS:
                return [(x, y + 26, x + 14, y + 38)]
            return [(x, y + 14, x + 14, y + 26), (x, y + 38, x + 14, y + 50)]
        if block["kind"] == "output":
            return [(x, y + 16, x + 12, y + 28)]
        return []

    def formula_port_position(
        self, block_id: int, direction: str, index: int | None = None
    ) -> tuple[float, float]:
        block = self.formula_blocks[block_id]
        x, y = float(block["x"]), float(block["y"])
        if direction == "out":
            if block["kind"] == "operation":
                return x + 88, y + 32
            width, height = self.formula_block_size(block)
            return x + width, y + height / 2
        if block["kind"] == "output":
            return x, y + 22
        if block["operation"] in self._UNARY_OPERATIONS:
            return x, y + 32
        return x, y + (20 if index == 0 else 44)

    def formula_frame_port_position(
        self, frame_id: int, direction: str, index: int
    ) -> tuple[float, float]:
        """Координата публичного порта на стенке или компактном фрейме."""
        frame = self.formula_frames[frame_id]
        x, y = float(frame.get("x", 40.0)), float(frame.get("y", 40.0))
        if bool(frame.get("collapsed")):
            width, _height = self.formula_frame_compact_size(frame)
            port_y = y + 34.0 + index * 21.0
            return (x, port_y) if direction == "in" else (x + width, port_y)
        x, y, width, height = self.formula_frame_bounds(frame)
        inputs = len(frame.get("inputs", []))
        outputs = len(frame.get("outputs", []))
        rows = max(1, inputs, outputs)
        top, bottom = y + 42.0, y + height - 16.0
        port_y = (top + bottom) / 2 if rows == 1 else top + (bottom - top) * index / (rows - 1)
        return (x, port_y) if direction == "in" else (x + width, port_y)

    def formula_source_port_position(self, source_id: int) -> tuple[float, float]:
        return self.formula_port_position(source_id, "out")

    def formula_target_port_position(self, target_id: int, index: int) -> tuple[float, float]:
        return self.formula_port_position(target_id, "in", index)

    def formula_connection_port_positions(
        self, source_id: int, target_id: int, index: int
    ) -> tuple[float, float, float, float]:
        """Концы провода с учётом перехода через стенки фреймов."""
        output_proxy = self.frame_output_for_connection(source_id, target_id)
        input_proxy = self.frame_input_for_connection(source_id, target_id, index)
        source_x, source_y = (
            self.formula_frame_port_position(output_proxy[0], "out", output_proxy[1])
            if output_proxy is not None
            else self.formula_port_position(source_id, "out")
        )
        target_x, target_y = (
            self.formula_frame_port_position(input_proxy[0], "in", input_proxy[1])
            if input_proxy is not None
            else self.formula_port_position(target_id, "in", index)
        )
        return source_x, source_y, target_x, target_y

    def draw_formula_port(self, block_id: int, direction: str, index: int | None) -> None:
        canvas = self.formula_canvas
        x, y = self.formula_port_position(block_id, direction, index)
        suffix = "out" if direction == "out" else f"in_{index}"
        tag = f"port_{block_id}_{suffix}"
        missing_input = direction == "in" and not self.formula_input_is_connected(block_id, index)
        color = "#f3a530" if direction == "out" else "#f1a21a" if missing_input else "#4285c5"
        fill = "#fff8e7" if missing_input else "#ffffff"
        canvas.create_oval(
            x - 6,
            y - 6,
            x + 6,
            y + 6,
            fill=fill,
            outline=color,
            width=2,
            tags=("formula_item", f"block_{block_id}", "formula_port", tag),
        )
        if direction == "out":
            canvas.tag_bind(
                tag,
                "<ButtonPress-1>",
                lambda event, bid=block_id: self.start_formula_wire(event, bid, "out", None),
            )
        else:
            canvas.tag_bind(
                tag,
                "<ButtonPress-1>",
                lambda event, bid=block_id, idx=index: self.start_formula_wire(
                    event, bid, "in", idx
                ),
            )
        canvas.tag_bind(
            tag, "<Enter>", lambda _event, item=tag: canvas.itemconfigure(item, fill="#fff1bd")
        )
        canvas.tag_bind(
            tag,
            "<Leave>",
            lambda _event, item=tag, value=fill: canvas.itemconfigure(item, fill=value),
        )

    def draw_formula_frame_port(
        self,
        frame_id: int,
        direction: str,
        index: int,
        endpoint_id: int | None,
        endpoint_index: int | None = None,
    ) -> None:
        """Порт на компактном представлении фрейма.

        Снаружи это обычный порт. Внутри он перенаправляется на выбранный
        блок, поэтому и цепочки из нескольких фреймов пересчитываются сразу.
        """
        canvas = self.formula_canvas
        x, y = self.formula_frame_port_position(frame_id, direction, index)
        tag = f"frame_port_{frame_id}_{direction}_{index}"
        color = "#f3a530" if direction == "out" else "#4285c5"
        canvas.create_oval(
            x - 6,
            y - 6,
            x + 6,
            y + 6,
            fill="#ffffff",
            outline=color,
            width=2,
            tags=("formula_item", f"frame_{frame_id}", "formula_port", tag),
        )
        if isinstance(endpoint_id, int) and endpoint_id in self.formula_blocks:
            if direction == "out":
                canvas.tag_bind(
                    tag,
                    "<ButtonPress-1>",
                    lambda event, bid=endpoint_id, fid=frame_id, slot=index: (
                        self.start_formula_wire(event, bid, "out", None, (fid, "out", slot))
                    ),
                )
            elif endpoint_index is not None:
                canvas.tag_bind(
                    tag,
                    "<ButtonPress-1>",
                    lambda event, bid=endpoint_id, slot=endpoint_index: self.start_formula_wire(
                        event, bid, "in", slot, (frame_id, "in", index)
                    ),
                )
        canvas.tag_bind(
            tag, "<Enter>", lambda _event, item=tag: canvas.itemconfigure(item, fill="#fff4cf")
        )
        canvas.tag_bind(
            tag, "<Leave>", lambda _event, item=tag: canvas.itemconfigure(item, fill="#ffffff")
        )

    @staticmethod
    def formula_wire_key(target_id: int, index: int) -> str:
        return f"{target_id}_{index}"

    def automatic_wire_points(self, start, end):
        obstacles = []
        for bid, block in self.formula_blocks.items():
            if not self.hidden_formula_block(bid):
                w, h = self.formula_block_size(block)
                x, y = float(block["x"]), float(block["y"])
                obstacles.append((x, y, x + w, y + h))
        for frame in self.formula_frames.values():
            if frame.get("collapsed"):
                w, h = self.formula_frame_compact_size(frame)
                x, y = float(frame["x"]), float(frame["y"])
            else:
                x, y, w, h = self.formula_frame_bounds(frame)
                # An internal connection is allowed inside its own frame. For
                # external wires, the frame body is an obstacle like any block.
                if all(x <= px <= x + w and y <= py <= y + h for px, py in (start, end)) or any(
                    x < px < x + w and y < py < y + h for px, py in (start, end)
                ):
                    continue
            obstacles.append((x, y, x + w, y + h))
        obstacles.extend(
            (float(c["x"]), float(c["y"]), float(c["x"]) + 170, float(c["y"]) + 50)
            for c in self.formula_comments.values()
        )
        key = (start, end, tuple(obstacles))
        if key not in self._automatic_paths:
            points = route_orthogonal(start, end, obstacles, self._occupied_wire_segments)
            self._automatic_paths[key] = points
            self._occupied_wire_segments.extend(segments(points))
        return self._automatic_paths[key]

    def formula_wire_points(self, source_id: int, target_id: int, index: int) -> list[float]:
        source_x, source_y, target_x, target_y = self.formula_connection_port_positions(
            source_id, target_id, index
        )
        key = self.formula_wire_key(target_id, index)
        route = self.wire_routes.get(key)
        if route is None:
            if self.document.auto_route:
                return self.automatic_wire_points((source_x, source_y), (target_x, target_y))
            # Обычный провод — короткая «скоба» из трёх ортогональных отрезков.
            # Более длинный маршрут появляется только после ручного переноса узла.
            middle_x = (source_x + target_x) / 2
            return [source_x, source_y, middle_x, source_y, middle_x, target_y, target_x, target_y]
        middle_x, middle_y = route
        return [
            source_x,
            source_y,
            middle_x,
            source_y,
            middle_x,
            middle_y,
            target_x,
            middle_y,
            target_x,
            target_y,
        ]

    def formula_wire_handle_position(
        self, source_id: int, target_id: int, index: int
    ) -> tuple[float, float]:
        key = self.formula_wire_key(target_id, index)
        route = self.wire_routes.get(key)
        if route is not None:
            return route
        if self.document.auto_route:
            return wire_midpoint(self.formula_wire_points(source_id, target_id, index))
        source_x, source_y, target_x, target_y = self.formula_connection_port_positions(
            source_id, target_id, index
        )
        return (source_x + target_x) / 2, (source_y + target_y) / 2

    def formula_wire_color(self, source_id: int) -> str:
        source = self.formula_blocks[source_id]
        if source["kind"] == "area":
            integral = self.integral_by_id(source.get("integral_id"))
            return str(integral["color"]) if integral else "#6f8ca6"
        if source["kind"] == "constant":
            return "#7b61ff"
        return "#48a942"

    def draw_formula_connections(self) -> None:
        """Ломаные LabVIEW-проводки, всегда состоящие только из H/V-сегментов."""
        canvas = self.formula_canvas
        for target_id, target in self.formula_blocks.items():
            sources = (
                list(target.get("inputs", []))
                if target["kind"] == "operation"
                else [target.get("input")]
            )
            for index, source_id in enumerate(sources):
                if not isinstance(source_id, int) or source_id not in self.formula_blocks:
                    continue
                # Внутренние проводки свёрнутого фрейма не должны торчать
                # наружу. Видны только связи, выведенные через его порты.
                if (
                    self.hidden_formula_block(source_id)
                    and self.frame_output_for_connection(source_id, target_id) is None
                ):
                    continue
                if (
                    self.hidden_formula_block(target_id)
                    and self.frame_input_for_connection(source_id, target_id, index) is None
                ):
                    continue
                key = self.formula_wire_key(target_id, index)
                points = self.formula_wire_points(source_id, target_id, index)
                canvas.create_line(
                    *points,
                    fill=self.formula_wire_color(source_id),
                    width=1.5,
                    joinstyle="round",
                    tags=("formula_item", "formula_wire", f"wire_line_{key}"),
                )
                middle_x, middle_y = self.formula_wire_handle_position(source_id, target_id, index)
                handle_tag = f"wire_handle_{key}"
                canvas.create_rectangle(
                    middle_x - 3,
                    middle_y - 3,
                    middle_x + 3,
                    middle_y + 3,
                    fill="#ffffff",
                    outline=self.formula_wire_color(source_id),
                    width=1,
                    tags=("formula_item", "formula_wire", "wire_handle", handle_tag),
                )
                canvas.tag_bind(
                    handle_tag,
                    "<ButtonPress-1>",
                    lambda event, tid=target_id, idx=index: self.start_wire_handle_drag(
                        event, tid, idx
                    ),
                )
                canvas.tag_bind(handle_tag, "<B1-Motion>", self.drag_wire_handle)
                canvas.tag_bind(handle_tag, "<ButtonRelease-1>", self.drop_wire_handle)
                canvas.tag_bind(
                    handle_tag,
                    "<Button-3>",
                    lambda event, tid=target_id, idx=index: self.formula_wire_menu(event, tid, idx),
                )
                canvas.tag_bind(
                    f"wire_line_{key}",
                    "<Button-3>",
                    lambda event, tid=target_id, idx=index: self.formula_wire_menu(event, tid, idx),
                )

    def formula_wire_menu(self, event, target_id: int, index: int) -> str:
        """Контекстное меню отдельного провода, не затрагивающее остальные связи."""
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(
            label="Отсоединить провод",
            command=lambda: self.disconnect_formula_wire(target_id, index),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def disconnect_formula_wire(self, target_id: int, index: int) -> None:
        target = self.formula_blocks.get(target_id)
        if target is None:
            return
        previous = self.capture_formula_state()
        if target["kind"] == "operation" and 0 <= index < len(target.get("inputs", [])):
            target["inputs"][index] = None
        elif target["kind"] == "output" and index == 0:
            target["input"] = None
        else:
            return
        self.wire_routes.pop(self.formula_wire_key(target_id, index), None)
        self.formula_status.set("Провод отсоединён.")
        self.render_formula_blocks()
        self.remember_formula_state(previous, "отсоединён провод")

    def formula_block_label(self, block_id: object) -> str:
        if not isinstance(block_id, int) or block_id not in self.formula_blocks:
            return "вставьте блок"
        block = self.formula_blocks[block_id]
        kind = block["kind"]
        if kind == "area":
            return f"I{self.formula_area_number(block)}"
        if kind == "constant":
            return self.format_formula_value(block.get("value"))
        if kind == "output":
            return str(block.get("name") or "Вывод")
        return f"результат {block_id}"

    def formula_area_number(self, block: dict[str, object]) -> str:
        saved = block.get("area_number")
        if isinstance(saved, int) and saved > 0:
            return str(saved)
        return self.integral_number(block.get("integral_id"))

    def start_formula_drag(self, event, block_id: int) -> str | None:
        x, y = self.formula_canvas.canvasx(event.x), self.formula_canvas.canvasy(event.y)
        self.formula_canvas.focus_set()
        if self.formula_tool == "comment":
            self.add_formula_comment_at(float(x), float(y))
            return "break"
        if self.formula_tool == "select":
            if event.state & 0x0001:
                if block_id in self.selected_formula_blocks:
                    self.selected_formula_blocks.remove(block_id)
                else:
                    self.selected_formula_blocks.add(block_id)
                self.render_formula_blocks()
                return "break"
            if block_id not in self.selected_formula_blocks:
                self.selected_formula_blocks = {block_id}
            self._formula_drag = {
                "id": block_id,
                "x": float(x),
                "y": float(y),
                "group": True,
                "history": self.capture_formula_state(),
            }
            self.render_formula_blocks()
            return "break"
        self._formula_drag = {
            "id": block_id,
            "x": float(x),
            "y": float(y),
            "history": self.capture_formula_state(),
        }
        self.formula_canvas.tag_raise(f"block_{block_id}")
        return "break"

    def drag_formula_block(self, event) -> str | None:
        if self._formula_drag is None:
            return None
        x, y = self.formula_canvas.canvasx(event.x), self.formula_canvas.canvasy(event.y)
        if self._formula_drag.get("group"):
            self.move_selected_formula_blocks(float(x), float(y))
            return "break"
        self.move_formula_block_to(float(x), float(y))
        return "break"

    def move_selected_formula_blocks(self, x: float, y: float) -> None:
        if self._formula_drag is None:
            return
        previous_x, previous_y = float(self._formula_drag["x"]), float(self._formula_drag["y"])
        dx, dy = x - previous_x, y - previous_y
        world_width, world_height = self._formula_world
        for block_id in self.selected_formula_blocks:
            block = self.formula_blocks.get(block_id)
            if block is None:
                continue
            width, height = self.formula_block_size(block)
            old_x, old_y = float(block["x"]), float(block["y"])
            next_x = min(max(10.0, old_x + dx), world_width - width - 10.0)
            next_y = min(max(10.0, old_y + dy), world_height - height - 10.0)
            block["x"], block["y"] = next_x, next_y
            self.formula_canvas.move(f"block_{block_id}", next_x - old_x, next_y - old_y)
        self._formula_drag["x"], self._formula_drag["y"] = x, y

    def move_formula_block_to(self, x: float, y: float) -> None:
        """Передвинуть блок в координатах рабочего холста, не давая ему потеряться."""
        if self._formula_drag is None:
            return
        block_id = int(self._formula_drag["id"])
        block = self.formula_blocks[block_id]
        width, height = self.formula_block_size(block)
        dx, dy = x - float(self._formula_drag["x"]), y - float(self._formula_drag["y"])
        world_width, world_height = self._formula_world
        next_x = min(max(10.0, float(block["x"]) + dx), world_width - width - 10.0)
        next_y = min(max(10.0, float(block["y"]) + dy), world_height - height - 10.0)
        dx, dy = next_x - float(block["x"]), next_y - float(block["y"])
        block["x"], block["y"] = next_x, next_y
        self._formula_drag["x"], self._formula_drag["y"] = x, y
        self.formula_canvas.move(f"block_{block_id}", dx, dy)

    def drop_formula_block(self, event) -> str | None:
        if self._formula_drag is None:
            return None
        previous = self._formula_drag.get("history")
        self._formula_drag = None
        # После перемещения блока проводки и порты перестраиваются по новой
        # геометрии; соединение создаётся только через сами порты.
        self.render_formula_blocks()
        if isinstance(previous, dict):
            self.remember_formula_state(previous, "перемещён блок")
        return "break"

    def formula_port_under_cursor(
        self, x: float, y: float, direction: str
    ) -> tuple[int, int | None] | None:
        """Найти ближайший совместимый порт в радиусе его кружка."""
        # Сначала проверяем порты стенок и компактные фреймы: они
        # перенаправляют провод на реальный внутренний блок.
        for frame_id, frame in reversed(list(self.formula_frames.items())):
            ports = frame.get("outputs", []) if direction == "out" else frame.get("inputs", [])
            for port_index, port in enumerate(ports):
                if not isinstance(port, dict):
                    continue
                port_x, port_y = self.formula_frame_port_position(frame_id, direction, port_index)
                if (port_x - x) ** 2 + (port_y - y) ** 2 > 12**2:
                    continue
                if direction == "out":
                    source_id = port.get("source_id")
                    if isinstance(source_id, int) and source_id in self.formula_blocks:
                        return source_id, None
                else:
                    target_id, index = port.get("target_id"), port.get("index")
                    if (
                        isinstance(target_id, int)
                        and isinstance(index, int)
                        and target_id in self.formula_blocks
                    ):
                        return target_id, index
        for block_id, block in reversed(list(self.formula_blocks.items())):
            if self.hidden_formula_block(block_id):
                continue
            if direction == "out":
                if block["kind"] == "output":
                    continue
                port_x, port_y = self.formula_port_position(block_id, "out")
                if (port_x - x) ** 2 + (port_y - y) ** 2 <= 12**2:
                    return block_id, None
            elif block["kind"] in {"operation", "output"}:
                count = len(block.get("inputs", [])) if block["kind"] == "operation" else 1
                for index in range(count):
                    port_x, port_y = self.formula_port_position(block_id, "in", index)
                    if (port_x - x) ** 2 + (port_y - y) ** 2 <= 12**2:
                        return block_id, index
        return None

    def start_formula_wire(
        self,
        event,
        block_id: int,
        direction: str,
        index: int | None,
        frame_port: tuple[int, str, int] | None = None,
    ) -> None:
        if direction == "out" and self.formula_blocks[block_id]["kind"] == "output":
            return
        self._formula_drag = None
        self._wire_drag = {
            "direction": direction,
            "block_id": block_id,
            "index": index,
            "frame_port": frame_port,
        }
        x, y = self.formula_canvas.canvasx(event.x), self.formula_canvas.canvasy(event.y)
        self.draw_wire_preview(float(x), float(y))

    def wire_preview_points(self, x: float, y: float) -> list[float]:
        assert self._wire_drag is not None
        block_id = int(self._wire_drag["block_id"])
        index = self._wire_drag["index"]
        frame_port = self._wire_drag.get("frame_port")
        if self._wire_drag["direction"] == "out":
            start_x, start_y = (
                self.formula_frame_port_position(*frame_port)
                if isinstance(frame_port, tuple)
                else self.formula_source_port_position(block_id)
            )
            end_x, end_y = x, y
        else:
            start_x, start_y = x, y
            end_x, end_y = (
                self.formula_frame_port_position(*frame_port)
                if isinstance(frame_port, tuple)
                else self.formula_target_port_position(block_id, int(index))
            )
        middle_x = (start_x + end_x) / 2
        return [start_x, start_y, middle_x, start_y, middle_x, end_y, end_x, end_y]

    def draw_wire_preview(self, x: float, y: float) -> None:
        canvas = self.formula_canvas
        canvas.delete("wire_preview")
        if self._wire_drag is not None:
            canvas.create_line(
                *self.wire_preview_points(x, y),
                fill="#f6b73c",
                width=2,
                dash=(4, 3),
                tags="wire_preview",
            )

    def drag_formula_wire(self, event) -> None:
        if self._wire_drag is None:
            return
        x, y = self.formula_canvas.canvasx(event.x), self.formula_canvas.canvasy(event.y)
        self.draw_wire_preview(float(x), float(y))

    def connect_formula_wire(self, source_id: int, target_id: int, index: int) -> None:
        previous = self.capture_formula_state()
        target = self.formula_blocks[target_id]
        if target["kind"] == "operation":
            target["inputs"][index] = source_id
        else:
            target["input"] = source_id
        self.wire_routes.pop(self.formula_wire_key(target_id, index), None)
        self.formula_status.set("Провод подключён — результат пересчитан.")
        self.render_formula_blocks()
        self.remember_formula_state(previous, "подключён провод")

    def drop_formula_wire(self, event) -> None:
        if self._wire_drag is None:
            return
        x, y = self.formula_canvas.canvasx(event.x), self.formula_canvas.canvasy(event.y)
        drag = self._wire_drag
        self._wire_drag = None
        self.formula_canvas.delete("wire_preview")
        if drag["direction"] == "out":
            source_id = int(drag["block_id"])
            target_port = self.formula_port_under_cursor(float(x), float(y), "in")
            if target_port is None:
                return
            target_id, index = target_port
        else:
            target_id, index = int(drag["block_id"]), int(drag["index"])
            source_port = self.formula_port_under_cursor(float(x), float(y), "out")
            if source_port is None:
                return
            source_id, _ = source_port
        assert index is not None
        if source_id == target_id or self.formula_depends_on(source_id, target_id):
            self.formula_status.set("Нельзя создать циклическую формулу.")
            return
        self.connect_formula_wire(source_id, target_id, index)

    def start_wire_handle_drag(self, event, target_id: int, index: int) -> None:
        self._wire_handle_drag = {
            "target_id": target_id,
            "index": index,
            "history": self.capture_formula_state(),
        }

    def drag_wire_handle(self, event) -> None:
        if self._wire_handle_drag is None:
            return
        target_id, index = (
            int(self._wire_handle_drag["target_id"]),
            int(self._wire_handle_drag["index"]),
        )
        x, y = self.formula_canvas.canvasx(event.x), self.formula_canvas.canvasy(event.y)
        world_width, world_height = self._formula_world
        route = (
            min(max(8.0, float(x)), world_width - 8.0),
            min(max(8.0, float(y)), world_height - 8.0),
        )
        key = self.formula_wire_key(target_id, index)
        self.wire_routes[key] = route
        target = self.formula_blocks.get(target_id)
        source_id = (
            target.get("inputs", [])[index]
            if target and target["kind"] == "operation"
            else target.get("input")
            if target
            else None
        )
        if not isinstance(source_id, int):
            return
        points = self.formula_wire_points(source_id, target_id, index)
        self.formula_canvas.coords(f"wire_line_{key}", *points)
        self.formula_canvas.coords(
            f"wire_handle_{key}", route[0] - 3, route[1] - 3, route[0] + 3, route[1] + 3
        )

    def drop_wire_handle(self, _event) -> None:
        if self._wire_handle_drag is None:
            return
        previous = self._wire_handle_drag.get("history")
        self._wire_handle_drag = None
        if isinstance(previous, dict):
            self.remember_formula_state(previous, "изменён маршрут провода")

    def formula_depends_on(
        self, block_id: int, searched_id: int, seen: set[int] | None = None
    ) -> bool:
        """Проверка циклов: источник не может зависеть от будущего получателя."""
        if block_id == searched_id:
            return True
        if block_id not in self.formula_blocks:
            return False
        seen = set() if seen is None else seen
        if block_id in seen:
            return False
        seen.add(block_id)
        block = self.formula_blocks[block_id]
        linked = block.get("inputs", []) if block["kind"] == "operation" else [block.get("input")]
        return any(
            isinstance(child, int) and self.formula_depends_on(child, searched_id, seen.copy())
            for child in linked
        )

    def candidate_formula_slot(self, x: float, y: float, source_id: int) -> tuple[int, int] | None:
        """Вернуть слот под указателем, если его можно безопасно соединить."""
        for target_id, target in reversed(list(self.formula_blocks.items())):
            if target_id == source_id or target["kind"] not in {"operation", "output"}:
                continue
            for index, (left, top, right, bottom) in enumerate(self.formula_slot_rects(target)):
                if left <= x <= right and top <= y <= bottom:
                    if self.formula_depends_on(source_id, target_id):
                        return None
                    return target_id, index
        return None

    def highlight_formula_slot(self, x: float, y: float, source_id: int) -> None:
        canvas = self.formula_canvas
        canvas.itemconfigure("input_slot_shape", outline="#2d8734", width=1)
        target = self.candidate_formula_slot(x, y, source_id)
        if target is not None:
            target_id, index = target
            canvas.itemconfigure(f"slot_{target_id}_{index}_shape", outline="#ffd24a", width=3)

    def dock_formula_source(self, source_id: int, target_id: int) -> None:
        """После соединения источник становится рядом со слотом, а не закрывает его."""
        source, target = self.formula_blocks[source_id], self.formula_blocks[target_id]
        source_width, source_height = self.formula_block_size(source)
        target_width, _target_height = self.formula_block_size(target)
        desired_x = float(target["x"]) - source_width - 28.0
        if desired_x < 12.0:
            desired_x = float(target["x"]) + target_width + 28.0
        world_width, world_height = self._formula_world
        source["x"] = min(max(12.0, desired_x), world_width - source_width - 12.0)
        source["y"] = min(max(12.0, float(target["y"]) + 18.0), world_height - source_height - 12.0)

    def drop_formula_block_at(self, x: float, y: float) -> None:
        if self._formula_drag is None:
            return
        source_id = int(self._formula_drag["id"])
        source = self.formula_blocks[source_id]
        candidate = (
            None if source["kind"] == "output" else self.candidate_formula_slot(x, y, source_id)
        )
        if candidate is not None:
            target_id, index = candidate
            target = self.formula_blocks[target_id]
            if target["kind"] == "operation":
                target["inputs"][index] = source_id
            else:
                target["input"] = source_id
            self.dock_formula_source(source_id, target_id)
            self.formula_status.set("Связь создана — результат пересчитан.")
        elif source["kind"] != "output":
            # Указатель на слоте, но соединение отклонено из-за цикла.
            for target_id, target in self.formula_blocks.items():
                if target_id == source_id:
                    continue
                if any(
                    left <= x <= right and top <= y <= bottom
                    for left, top, right, bottom in self.formula_slot_rects(target)
                ):
                    self.formula_status.set("Нельзя создать циклическую формулу.")
                    break
        self._formula_drag = None
        self.formula_canvas.itemconfigure("input_slot_shape", outline="#2d8734", width=1)
        self.render_formula_blocks()

    def formula_menu(self, event) -> str | None:
        # Меню рабочего поля — строго реакция на правую кнопку мыши. Это
        # дополнительно страхует обработчик от событий клавиатуры и кнопки
        # «Удалить выделенное».
        if getattr(event, "num", None) != 3:
            return
        x, y = (
            float(self.formula_canvas.canvasx(event.x)),
            float(self.formula_canvas.canvasy(event.y)),
        )
        # Проверка геометрии нужна, потому что Canvas может считать текущим
        # элементом пунктирный внутренний провод, а не саму рамку.
        frame_at_pointer = self.formula_frame_at(x, y)
        if frame_at_pointer is not None:
            self.formula_frame_menu(event, frame_at_pointer)
            return "break"
        current = self.formula_canvas.find_withtag("current")
        block_id = None
        frame_id = None
        if current:
            for tag in self.formula_canvas.gettags(current[0]):
                if tag.startswith("block_"):
                    block_id = int(tag.split("_")[1])
                elif tag.startswith("frame_") and tag[6:].isdigit():
                    frame_id = int(tag[6:])
        if frame_id is not None:
            self.formula_frame_menu(event, frame_id)
            return "break"
        menu = tk.Menu(self, tearoff=False)
        if block_id is not None:
            block = self.formula_blocks[block_id]
            if block["kind"] == "constant":
                menu.add_command(
                    label="Изменить константу…",
                    command=lambda: self.edit_formula_constant(block_id),
                )
            elif block["kind"] == "output":
                menu.add_command(
                    label="Переименовать вывод…",
                    command=lambda: self.rename_formula_output(block_id),
                )
            if block["kind"] in {"operation", "output"}:
                menu.add_command(
                    label="Отсоединить входы",
                    command=lambda: self.disconnect_formula_block(block_id),
                )
            menu.add_command(
                label="Дублировать", command=lambda: self.duplicate_formula_block(block_id)
            )
            menu.add_separator()
            menu.add_command(
                label="Удалить блок", command=lambda: self.delete_formula_block(block_id)
            )
        else:
            area_menu = tk.Menu(menu, tearoff=False)
            for item in self.ordered_integrals():
                number = self.integral_number(item["id"])
                area_menu.add_command(
                    label=f"Площадь I{number}",
                    command=lambda i=int(item["id"]): self.add_formula_block(
                        "area", x, y, integral_id=i
                    ),
                )
            menu.add_cascade(label="Площадь", menu=area_menu)
            menu.add_command(label="Константа…", command=lambda: self.add_formula_constant(x, y))
            operation_menu = tk.Menu(menu, tearoff=False)
            for operation in self._OPERATION_NAMES:
                operation_menu.add_command(
                    label=operation,
                    command=lambda op=operation: self.add_formula_block(
                        "operation", x, y, operation=op
                    ),
                )
            menu.add_cascade(label="Мат. операция", menu=operation_menu)
            menu.add_command(
                label="Блок вывода", command=lambda: self.add_formula_block("output", x, y)
            )
            if self.selected_formula_blocks:
                menu.add_separator()
                menu.add_command(
                    label="Создать фрейм из выделенных", command=self.create_formula_frame
                )
            menu.add_separator()
            menu.add_command(label="Clean — упорядочить схему", command=self.clean_formula_scene)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def edit_formula_constant(self, block_id: int) -> None:
        current = float(self.formula_blocks[block_id]["value"])
        value = self.ask_formula_number("Константа", "Новое значение:", current)
        if value is not None:
            previous = self.capture_formula_state()
            self.formula_blocks[block_id]["value"] = value
            self.refresh_formula_workspace()
            self.remember_formula_state(previous, "изменена константа")

    def rename_formula_output(self, block_id: int) -> None:
        current = str(self.formula_blocks[block_id].get("name") or "Вывод")
        name = simpledialog.askstring(
            "Вывод", "Название карточки результата:", initialvalue=current, parent=self
        )
        if name and name.strip():
            previous = self.capture_formula_state()
            self.formula_blocks[block_id]["name"] = name.strip()
            self.formula_blocks[block_id]["auto_name"] = False
            self.render_formula_blocks()
            self.remember_formula_state(previous, "переименован результат")

    def renumber_formula_outputs(self) -> None:
        """Автоматические имена нумеруются только среди блоков вывода."""
        outputs = [block for block in self.formula_blocks.values() if block["kind"] == "output"]
        for number, block in enumerate(outputs, start=1):
            if block.get("auto_name", True):
                block["name"] = "Вывод" if number == 1 else f"Вывод {number}"

    def disconnect_formula_block(self, block_id: int) -> None:
        block = self.formula_blocks.get(block_id)
        if block is None:
            return
        previous = self.capture_formula_state()
        if block["kind"] == "operation":
            block["inputs"] = [None for _ in block["inputs"]]
            for index in range(len(block["inputs"])):
                self.wire_routes.pop(self.formula_wire_key(block_id, index), None)
        elif block["kind"] == "output":
            block["input"] = None
            self.wire_routes.pop(self.formula_wire_key(block_id, 0), None)
        self.render_formula_blocks()
        self.remember_formula_state(previous, "отсоединены входы блока")

    def duplicate_formula_block(self, block_id: int) -> None:
        block = self.formula_blocks.get(block_id)
        if block is None:
            return
        x, y = float(block["x"]) + 36, float(block["y"]) + 36
        clone = self.add_formula_block(
            str(block["kind"]),
            x,
            y,
            integral_id=block.get("integral_id"),
            operation=block.get("operation"),
            value=block.get("value"),
            name=str(block.get("name") or "") + " копия" if block["kind"] == "output" else None,
        )
        if block["kind"] == "operation":
            self.formula_blocks[clone]["inputs"] = list(block.get("inputs", []))
        elif block["kind"] == "output":
            self.formula_blocks[clone]["input"] = block.get("input")
        self.render_formula_blocks()

    def delete_formula_block(self, block_id: int) -> None:
        if block_id not in self.formula_blocks:
            return
        previous = self.capture_formula_state()
        self.formula_blocks.pop(block_id, None)
        self.renumber_formula_outputs()
        for block in self.formula_blocks.values():
            if block.get("input") == block_id:
                block["input"] = None
            if "inputs" in block:
                block["inputs"] = [
                    None if value == block_id else value for value in block["inputs"]
                ]
        self.cleanup_formula_frames()
        self.cleanup_wire_routes()
        self.refresh_formula_workspace()
        self.remember_formula_state(previous, "удалён блок")

    def cleanup_wire_routes(self) -> None:
        """Убрать маршруты, для которых после удаления больше нет связи."""
        for key in list(self.wire_routes):
            try:
                target_text, index_text = key.rsplit("_", 1)
                target_id, index = int(target_text), int(index_text)
            except ValueError:
                self.wire_routes.pop(key, None)
                continue
            target = self.formula_blocks.get(target_id)
            if target is None:
                self.wire_routes.pop(key, None)
                continue
            inputs = target.get("inputs", [])
            source_id = (
                inputs[index]
                if target["kind"] == "operation" and index < len(inputs)
                else target.get("input")
                if target["kind"] == "output" and index == 0
                else None
            )
            if not isinstance(source_id, int) or source_id not in self.formula_blocks:
                self.wire_routes.pop(key, None)

    def refresh_result_panel(self) -> None:
        if not hasattr(self, "result_panel"):
            return
        theme = self.formula_theme
        for child in self.result_panel.winfo_children():
            child.destroy()
        self.formula_results_table = None
        outputs = [
            (block_id, block)
            for block_id, block in self.formula_blocks.items()
            if block["kind"] == "output"
        ]
        if not outputs:
            empty = tk.Frame(
                self.result_panel,
                bg=PAGE,
                highlightbackground=BORDER,
                highlightthickness=1,
                padx=10,
                pady=10,
            )
            empty.pack(fill="x", pady=(0, 7))
            tk.Label(
                empty,
                text="Нет результата",
                bg=PAGE,
                fg=TEXT,
                font="AppHeadingFont",
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                empty,
                text="Добавьте карточку «Результат»\nи подключите её к схеме.",
                bg=PAGE,
                fg=MUTED,
                justify="left",
                anchor="w",
                font="AppSmallFont",
            ).pack(fill="x", pady=(4, 0))
            return
        # Для схемы, собранной на обзорном графике, отдельное число «Вывод»
        # неоднозначно: оно относится лишь к активной строке. Показываем
        # только таблицу для всех спектров этого обзора.
        comparison = self.comparison_record()
        if comparison is None:
            stored_ids = {
                block.get("comparison_id")
                for block in self.formula_blocks.values()
                if block.get("kind") == "area" and isinstance(block.get("comparison_id"), int)
            }
            if len(stored_ids) == 1:
                comparison = self.comparisons.get(next(iter(stored_ids)))
        comparison_spectrum_ids = (
            [
                int(value)
                for value in comparison.get("spectrum_ids", [])
                if int(value) in self.spectra
            ]
            if comparison is not None
            else []
        )
        multi_result = len(comparison_spectrum_ids) > 1

        if not multi_result:
            for number, (block_id, block) in enumerate(outputs, start=1):
                name = str(block.get("name") or ("Вывод" if number == 1 else f"Вывод {number}"))
                value = self.formula_value(block_id)
                state, diagnostic = self.formula_block_diagnostic(block_id)
                palette = {
                    "ready": (SURFACE, BORDER, SUCCESS, "Готово"),
                    "warning": (SURFACE, BORDER, WARNING, "Нужен вход"),
                    "error": (SURFACE, BORDER, ERROR, "Проверьте схему"),
                }
                bg, border, accent, badge = palette[state]
                card = tk.Frame(
                    self.result_panel,
                    bg=bg,
                    highlightbackground=border,
                    highlightthickness=1,
                    padx=10,
                    pady=9,
                )
                card.pack(fill="x", pady=(0, 7))
                title_row = tk.Frame(card, bg=bg)
                title_row.pack(fill="x")
                tk.Label(
                    title_row,
                    text=name,
                    bg=bg,
                    fg=TEXT,
                    anchor="w",
                    font="TkDefaultFont",
                ).pack(side="left", fill="x", expand=True)
                tk.Label(
                    title_row,
                    text=badge,
                    bg=bg,
                    fg=accent,
                    anchor="e",
                    font="AppSmallFont",
                ).pack(side="right")
                value_row = tk.Frame(card, bg=bg)
                value_row.pack(fill="x", pady=(4, 0))
                tk.Label(
                    value_row,
                    text=self.format_formula_value(value),
                    bg=bg,
                    fg=TEXT,
                    font="AppValueFont",
                    anchor="w",
                ).pack(side="left", fill="x", expand=True)
                if state != "ready":
                    tk.Label(
                        card,
                        text=diagnostic,
                        bg=bg,
                        fg=accent,
                        anchor="w",
                        justify="left",
                        wraplength=170,
                        font="AppSmallFont",
                    ).pack(fill="x", pady=(5, 0))

        if multi_result:
            result_header = tk.Frame(self.result_panel, bg=theme["panel"])
            result_header.pack(fill="x", pady=(0, 4))
            tk.Label(
                result_header,
                text="РЕЗУЛЬТАТЫ ПО СПЕКТРАМ",
                bg=theme["panel"],
                fg=MUTED,
                font="AppSmallFont",
            ).pack(side="left")
            columns = tuple(f"out_{block_id}" for block_id, _block in outputs)
            table = ttk.Treeview(
                self.result_panel,
                columns=("spectrum",) + columns,
                show="headings",
                height=min(7, len(comparison_spectrum_ids)),
                selectmode="extended",
                style="Formula.Treeview",
            )
            table.heading("spectrum", text="Спектр")
            table.column("spectrum", width=105, anchor="w", stretch=True)
            for number, (block_id, block) in enumerate(outputs, start=1):
                name = str(block.get("name") or ("Вывод" if number == 1 else f"Вывод {number}"))
                column = f"out_{block_id}"
                table.heading(column, text=name)
                table.column(column, width=78, anchor="center", stretch=True)
            for row_number, spectrum_id in enumerate(comparison_spectrum_ids):
                record = self.spectra[spectrum_id]
                values = [str(record["name"])]
                values.extend(
                    self.format_formula_value(self.formula_value_for_record(block_id, record))
                    for block_id, _block in outputs
                )
                table.insert(
                    "", "end", values=values, tags=("even" if row_number % 2 == 0 else "odd",)
                )
            table.tag_configure("even", background=SURFACE)
            table.tag_configure("odd", background=SURFACE)
            self.formula_results_table = table
            table.bind("<Control-KeyPress>", self.copy_formula_results_table_shortcut)
            table.pack(fill="x", pady=(0, 4))

    def copy_formula_results_table(self, _event=None) -> str:
        """Скопировать результаты формулы по спектрам в Excel-совместимый TSV."""
        table = self.formula_results_table
        if table is None or not self.copy_treeview_rows(table, context="formula"):
            self.formula_status.set("Таблица результатов по спектрам пока не построена.")
        return "break"

    def copy_formula_results_table_shortcut(self, event) -> str | None:
        keysym = str(getattr(event, "keysym", "")).lower()
        is_c_key = keysym in {"c", "cyrillic_es", "с"} or int(getattr(event, "keycode", -1)) == 67
        return self.copy_formula_results_table(event) if is_c_key else None
