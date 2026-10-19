"""The everyday calculation interface: choose a formula and read the results."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from formula_engine import format_value
from ui_theme import PAGE, TEXT, MUTED


class CalculationsView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=PAGE)
        self.app = app
        self.pack(fill="both", expand=True)
        header = tk.Frame(self, bg=PAGE, padx=10, pady=8)
        header.pack(fill="x")
        tk.Label(
            header,
            text="Расчёты по интегралам",
            font="AppTitleFont",
            fg=TEXT,
            bg=PAGE,
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Выберите готовую формулу слева — результаты пересчитываются при обработке спектра.",
            font="TkDefaultFont",
            fg=MUTED,
            bg=PAGE,
        ).pack(anchor="w", pady=(6, 0))
        body = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=PAGE, bd=0, sashwidth=7)
        library = tk.LabelFrame(body, text="  Формулы  ", bg=PAGE, padx=8, pady=8)
        right = tk.LabelFrame(body, text="  Результаты  ", bg=PAGE, padx=8, pady=8)
        body.add(library, minsize=235, width=300)
        body.add(right, minsize=360)
        search_row = tk.Frame(library, bg=PAGE)
        search_row.pack(fill="x", pady=(10, 8))
        self.search = tk.StringVar()
        entry = ttk.Entry(search_row, textvariable=self.search)
        entry.pack(fill="x")
        self.search.trace_add("write", lambda *_: self.refresh_library())
        self.list = ttk.Treeview(
            library, columns=("name",), show="tree", selectmode="browse", height=10
        )
        self.list.bind("<<TreeviewSelect>>", self.select_preset)
        buttons = tk.Frame(library, bg=PAGE)
        buttons.pack(side="bottom", fill="x", pady=(8, 0))
        tk.Button(buttons, text="Импорт…", command=self.import_presets).pack(side="left")
        tk.Button(buttons, text="Обновить", command=self.reload_library).pack(side="right")
        self.list.pack(fill="both", expand=True)
        self.title = tk.StringVar(value="Выберите формулу слева")
        self.description = tk.StringVar(
            value="Выберите готовую формулу из списка и создайте области интегрирования на спектре."
        )
        self.inputs = tk.StringVar()
        tk.Label(
            right,
            textvariable=self.title,
            font="AppHeadingFont",
            bg=PAGE,
            fg=TEXT,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            right,
            textvariable=self.description,
            font="TkDefaultFont",
            bg=PAGE,
            fg=MUTED,
            anchor="w",
            wraplength=630,
            justify="left",
        ).pack(fill="x", pady=(6, 10))
        tk.Label(
            right,
            textvariable=self.inputs,
            font="TkDefaultFont",
            bg=PAGE,
            fg=TEXT,
            anchor="w",
            padx=10,
            pady=8,
        ).pack(fill="x", pady=(0, 12))
        table_area = tk.Frame(right, bg=PAGE)
        table_area.rowconfigure(0, weight=1)
        table_area.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(table_area, show="headings", selectmode="extended", height=8)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table_area, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(table_area, orient="horizontal", command=self.table.xview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.bind("<Control-KeyPress>", self.copy_shortcut)
        actions = tk.Frame(right, bg=PAGE)
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        tk.Button(actions, text="Пересчитать", command=app.refresh_formula_workspace).pack(
            side="left"
        )
        tk.Button(actions, text="Редактор формул…", command=app.open_formula_editor).pack(
            side="right"
        )
        tk.Label(
            right,
            text="Ctrl+C — копировать выбранные строки. Формулу можно изменить в отдельном редакторе.",
            font="AppSmallFont",
            bg=PAGE,
            fg=MUTED,
            anchor="w",
            wraplength=650,
        ).pack(side="bottom", fill="x", pady=(8, 0))
        table_area.pack(fill="both", expand=True)
        tk.Label(
            self,
            textvariable=app.formula_status,
            bg=PAGE,
            fg=MUTED,
            anchor="w",
            padx=18,
            pady=6,
        ).pack(side="bottom", fill="x")
        body.pack(fill="both", expand=True, padx=10, pady=8)
        self._refreshing = False
        self.refresh_library()

    def refresh_library(self):
        self._refreshing = True
        selected = self.app.active_preset_key
        self.list.delete(*self.list.get_children())
        query = self.search.get().strip().casefold()
        self.keys = {}
        for index, entry in enumerate(self.app.preset_library.entries.values()):
            if query and query not in entry.name.casefold():
                continue
            iid = f"preset_{index}"
            self.keys[iid] = entry.key
            self.list.insert("", "end", iid=iid, text=entry.name)
            if entry.key == selected:
                self.list.selection_set(iid)
        self._refreshing = False

    def select_preset(self, _event=None):
        if self._refreshing:
            return
        selection = self.list.selection()
        if selection:
            key = self.keys.get(selection[0])
            if key and key != self.app.active_preset_key:
                self.app.apply_calculation_preset(key)

    def reload_library(self):
        self.app.preset_library.refresh()
        self.refresh_library()

    def import_presets(self):
        paths = filedialog.askopenfilenames(
            parent=self, title="Добавить формулы расчётов", filetypes=(("Формулы NOX", "*.json"),)
        )
        errors, last = [], None
        for path in paths:
            try:
                last = self.app.preset_library.register(path, import_copy=True)
            except (OSError, ValueError, TypeError, KeyError) as error:
                errors.append(f"{path}: {error}")
        if last:
            self.app.apply_calculation_preset(last.key)
        self.refresh_library()
        if errors:
            messagebox.showerror("Импорт формул", "\n".join(errors), parent=self)

    def refresh_results(self):
        doc = self.app.formula_document
        self.title.set(doc.name if doc.blocks else "Выберите формулу слева")
        entry = self.app.preset_library.entries.get(self.app.active_preset_key)
        self.description.set(
            entry.description
            if entry
            else (
                "Собственный расчёт. Его можно сохранить как формулу в редакторе формул."
                if doc.blocks
                else "Выберите готовую формулу из списка и создайте области интегрирования на спектре."
            )
        )
        required = doc.area_numbers()
        available = {int(i["number"]) for i in self.app.formula_source_integrals()}
        missing = [i for i in required if i not in available]
        self.inputs.set(
            "Нужны: "
            + (", ".join(f"I{i}" for i in required) or "без интегралов")
            + ("  ·  Нет: " + ", ".join(f"I{i}" for i in missing) + " → 0" if missing else "")
        )
        rows = self.app.calculation_rows()
        outputs = [
            (bid, b.get("name") or f"Вывод {n + 1}")
            for n, (bid, b) in enumerate(
                (i for i in doc.blocks.items() if i[1]["kind"] == "output")
            )
        ]
        columns = ("spectrum",) + tuple(f"out_{bid}" for bid, _ in outputs) + ("status",)
        old_selected = {self.table.item(i, "values")[0] for i in self.table.selection()}
        self.table.delete(*self.table.get_children())
        self.table.configure(columns=columns)
        self.table.heading("spectrum", text="Спектр")
        self.table.column("spectrum", width=140, minwidth=90)
        for bid, name in outputs:
            col = f"out_{bid}"
            self.table.heading(col, text=name)
            self.table.column(col, width=145, minwidth=90, anchor="center")
        self.table.heading("status", text="Состояние")
        self.table.column("status", width=280, minwidth=180)
        for index, (record, results) in enumerate(rows):
            diagnostics = list(
                dict.fromkeys(r.message for _, _, r in results if r.status != "ready")
            )
            values = (
                [record["name"]]
                + [format_value(r.value) for _, _, r in results]
                + ["; ".join(diagnostics) or ("Готово" if outputs else "Нет вывода")]
            )
            iid = self.table.insert(
                "", "end", values=values, tags=("warning" if diagnostics else "normal",)
            )
            if record["name"] in old_selected:
                self.table.selection_add(iid)
        self.table.tag_configure("warning", foreground="#9b650d")

    def copy_shortcut(self, event):
        if (
            str(getattr(event, "keysym", "")).lower() in {"c", "cyrillic_es", "с"}
            or getattr(event, "keycode", None) == 67
        ):
            self.app.copy_treeview_rows(self.table, context="formula")
            return "break"
