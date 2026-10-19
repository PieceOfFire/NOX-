"""Operator-led series processing in the normal spectrum workspace.

Only the operator sets phase. Workers replay background/integration recipes on
detached records; accepting, skipping and updating the recipe are explicit.
"""

from __future__ import annotations

import copy
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from batch_session import BatchSession
from formula_document import FormulaDocument
from processing_templates import apply_template, make_template
from spectrum_library import next_attention_id, spectrum_label
from spin_backend import same_state
from ui_theme import MUTED, mark_primary
from workflow_tools import ResultsWindow


STATUS_LABELS = {
    "reference": "Образец",
    "awaiting_phase": "Нужна ручная фаза",
    "processing": "Фон и интегралы…",
    "review": "Проверить результат",
    "confirmed": "Подтверждён",
    "skipped": "Пропущен",
    "error": "Ошибка",
}


def record_token(record):
    """Cheap conservative GUI-edit detector; workers additionally compare full snapshots."""
    return (
        *(
            record.get(k)
            for k in ("phase0", "phase1", "phase_pivot", "ppm_shift", "integration_method")
        ),
        *(id(record.get(k)) for k in ("real", "imaginary", "corrected", "ppm")),
        repr(record.get("integrals", [])),
        tuple((key, id(value)) for key, value in record.get("spin_analyses", {}).items()),
    )


class BatchSetup(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self._import_job = None
        self._import_paths = []
        self.title("Himera · Обработать серию")
        self.geometry("780x600")
        self.minsize(620, 460)
        self.transient(app)
        self.grab_set()
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Выберите спектры и образец", font="AppTitleFont").pack(anchor="w")
        ttk.Label(
            body,
            text="Сначала обработайте образец. Для остальных: ручная фаза → фон и интегралы → подтверждение.",
            wraplength=720,
            foreground=MUTED,
        ).pack(anchor="w", pady=(6, 12))
        commands = ttk.Frame(body)
        commands.pack(fill="x")
        self.import_button = ttk.Button(commands, text="Добавить папки…", command=self.add_folders)
        self.import_button.pack(side="left")
        ttk.Button(
            commands,
            text="Выбрать все",
            command=lambda: self.table.selection_set(self.table.get_children()),
        ).pack(side="left", padx=6)
        table_body = ttk.Frame(body)
        table_body.pack(fill="both", expand=True, pady=10)
        self.table = ttk.Treeview(
            table_body, columns=("name",), show="headings", selectmode="extended", height=9
        )
        self.table.heading("name", text="Спектры (Ctrl / Shift — выбрать несколько)")
        self.table.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table_body, orient="vertical", command=self.table.yview)
        scroll.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scroll.set)
        self.import_note = tk.StringVar(self)
        ttk.Label(body, textvariable=self.import_note, foreground=MUTED).pack(anchor="w")
        self.table.bind("<<TreeviewSelect>>", self.update_reference)
        self.reference = ttk.Combobox(body, state="readonly")
        ttk.Label(body, text="Спектр-образец:").pack(anchor="w")
        self.reference.pack(fill="x", pady=(3, 10))
        self.formula = ttk.Combobox(body, state="readonly")
        ttk.Label(body, text="Формула расчёта:").pack(anchor="w")
        self.formula.pack(fill="x", pady=(3, 8))
        self.formula_entries = [None, "current"] + list(app.preset_library.entries)
        self.formula.configure(
            values=["Без формулы", "Текущая формула"]
            + [e.name for e in app.preset_library.entries.values()]
        )
        self.formula.current(1 if app.formula_document.blocks else 0)
        self.align = tk.BooleanVar(self, value=True)
        ttk.Checkbutton(
            body,
            text="Выравнивать X по видимому диапазону образца (до ±0,05 ppm)",
            variable=self.align,
        ).pack(anchor="w")
        ttk.Label(
            body,
            text="Автофаза и перенос фазы образца отключены. Уже обработанные спектры не меняются до применения.",
            wraplength=710,
            foreground=MUTED,
        ).pack(anchor="w", pady=6)
        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(6, 0))
        ttk.Button(footer, text="Отмена", command=self.destroy).pack(side="right")
        self.start_button = mark_primary(
            ttk.Button(footer, text="Начать с образца", command=self.start)
        )
        self.start_button.pack(side="right", padx=6)
        self.refresh()

    def refresh(self):
        self.table.delete(*self.table.get_children())
        for sid, record in self.app.spectra.items():
            self.table.insert("", "end", iid=str(sid), values=(spectrum_label(record),))
        self.table.selection_set(self.table.get_children())
        self.update_reference()

    def update_reference(self, _event=None):
        old_ids = getattr(self, "selected", [])
        old_index = self.reference.current()
        old_id = old_ids[old_index] if 0 <= old_index < len(old_ids) else None
        self.selected = [int(sid) for sid in self.table.selection()]
        labels = [spectrum_label(self.app.spectra[sid]) for sid in self.selected]
        self.reference.configure(values=labels)
        if old_id in self.selected:
            self.reference.current(self.selected.index(old_id))
        elif labels:
            index = (
                self.selected.index(self.app.active_spectrum_id)
                if self.app.active_spectrum_id in self.selected
                else 0
            )
            self.reference.current(index)
        else:
            self.reference.set("")

    def add_folders(self):
        from nox_app import choose_windows_folders

        paths = choose_windows_folders(self.winfo_id())
        if paths is None:
            path = filedialog.askdirectory(parent=self, title="Добавить Bruker-папку")
            paths = (path,) if path else ()
        self._import_paths = list(paths)
        self._import_count = 0
        self._import_total = len(self._import_paths)
        self._import_failed = 0
        self.import_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self._import_next()

    def _import_next(self):
        self._import_job = None
        if not self._import_paths:
            self.import_button.configure(state="normal")
            self.start_button.configure(state="normal")
            self.import_note.set(
                f"Добавление завершено: {self._import_count}; ошибок: {self._import_failed}"
            )
            self.refresh()
            return
        path = self._import_paths.pop(0)
        if self.app.add_spectrum_folder(path, show_error=False):
            self._import_count += 1
        else:
            self._import_failed += 1
        self.import_note.set(
            f"Добавление: {self._import_count + self._import_failed}/{self._import_total}"
        )
        self._import_job = self.after(1, self._import_next)

    def destroy(self):
        if self._import_job is not None:
            self.after_cancel(self._import_job)
        super().destroy()

    def start(self):
        self.update_reference()
        if not self.selected or self.reference.current() < 0:
            messagebox.showinfo("Серия", "Выберите спектры и образец.", parent=self)
            return
        entry = self.formula_entries[self.formula.current()]
        if entry == "current":
            formula = self.app.formula_preset_data()
        elif entry is None:
            formula = {}
        else:
            formula = copy.deepcopy(self.app.preset_library.entries[entry].preset)
        reference = self.selected[self.reference.current()]
        try:
            self.app.start_manual_batch(
                self.selected,
                reference_id=reference,
                formula=formula,
                options={"align": self.align.get()},
            )
        except ValueError as error:
            messagebox.showerror("Серия", str(error), parent=self)
            return
        self.destroy()


class BatchWorkflowMixin:
    def initialize_batch(self):
        self.batch_session = None
        self._batch_running = False
        self._batch_events = queue.Queue()
        self._batch_poll = None
        self._batch_tokens = {}
        self._batch_diagnostic_cache = {}
        self._batch_phase_templates = {}
        self._batch_queue_window = None
        self._batch_load_candidate = None
        self._batch_bar_hidden = False
        self._batch_library_signature = None

    def install_batch_controls(self):
        controls = self.zoom_button.master
        self.batch_button = tk.Button(controls, text="Серия…", command=self.open_manual_batch)
        self.batch_button.pack(side="right", padx=4)
        self.add_formula_tooltip(
            self.batch_button, "Обработать серию: ручная фаза, затем фон и интегралы по образцу"
        )
        self.batch_bar = ttk.Frame(self.spectrum_tab, padding=(10, 5))
        self.batch_heading = tk.StringVar(self)
        ttk.Label(self.batch_bar, textvariable=self.batch_heading, font="AppHeadingFont").pack(
            anchor="w"
        )
        self.batch_hint = tk.StringVar(self)
        ttk.Label(
            self.batch_bar, textvariable=self.batch_hint, foreground=MUTED, wraplength=1050
        ).pack(anchor="w", pady=3)
        row = ttk.Frame(self.batch_bar)
        row.pack(fill="x")
        ttk.Button(row, text="← Назад", command=lambda: self.batch_step(-1)).pack(side="left")
        self.batch_phase_button = ttk.Button(
            row, text="φ Фазировать", command=self.open_phase_dialog
        )
        self.batch_phase_button.pack(side="left", padx=4)
        self.batch_action_button = mark_primary(ttk.Button(row, command=self.batch_action))
        self.batch_action_button.pack(side="left", padx=4)
        ttk.Button(row, text="Пропустить →", command=self.batch_skip).pack(side="left", padx=4)
        self.batch_pause_button = ttk.Button(row, text="Пауза", command=self.batch_pause)
        self.batch_pause_button.pack(side="right", padx=4)
        ttk.Button(row, text="Очередь / результаты…", command=self.show_batch_queue).pack(
            side="right"
        )
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Обработать серию…", command=self.open_manual_batch)
        menu.add_command(label="Новая очередь…", command=lambda: self.open_manual_batch(new=True))
        menu.add_command(label="Очередь и результаты…", command=self.show_batch_queue)
        menu.add_command(label="Следующий требующий внимания", command=self.batch_next_attention)
        menu.add_separator()
        menu.add_command(
            label="Подтвердить и следующий", accelerator="Ctrl+Enter", command=self.batch_action
        )
        menu.add_command(
            label="Предыдущий спектр", accelerator="Alt+←", command=lambda: self.batch_step(-1)
        )
        menu.add_command(
            label="Следующий спектр", accelerator="Alt+→", command=lambda: self.batch_step(1)
        )
        menu.add_command(
            label="Пропустить спектр", accelerator="Ctrl+Shift+Enter", command=self.batch_skip
        )
        menu.add_separator()
        menu.add_command(
            label="Обновить шаблон для оставшихся по текущему спектру…",
            command=self.batch_update_template,
        )
        menu.add_separator()
        menu.add_command(label="Сохранить очередь в проекте…", command=self.save_project)
        menu.add_command(label="Скрыть панель очереди", command=self.hide_batch_bar)
        bar = self.nametowidget(self.cget("menu"))
        bar.add_cascade(label="Серия", menu=menu)
        for sequence, action in (
            ("<Control-Return>", "confirm"),
            ("<Control-Shift-Return>", "skip"),
            ("<Alt-Right>", "next"),
            ("<Alt-Left>", "previous"),
        ):
            self.bind_all(
                sequence,
                lambda event, command=action: self._batch_shortcut(event, command),
                add="+",
            )
        self.add_formula_tooltip(
            self.batch_action_button,
            "Ctrl+Enter — подтвердить проверенный результат и перейти дальше. "
            "Alt+←/→ — просмотр очереди; Ctrl+Shift+Enter — пропустить. В полях ввода сочетания не действуют.",
        )

    def _batch_shortcut(self, event, action):
        """Queue shortcuts never accept a result from text input or a child dialog."""
        if (
            not self.batch_session
            or self._batch_running
            or self._spectrum_dialog_open
            or self.grab_current() is not None
            or self.notebook.select() != str(self.spectrum_tab)
        ):
            return None
        widget = getattr(event, "widget", None) or self.focus_get()
        try:
            if widget is None or widget.winfo_toplevel() != self:
                return None
            if widget.winfo_class() in {
                "Entry",
                "TEntry",
                "Spinbox",
                "TSpinbox",
                "Text",
                "TCombobox",
            }:
                return None
        except tk.TclError:
            return None
        if action == "confirm":
            if self.batch_session.current_item["status"] not in {"review", "confirmed"}:
                self.status.set("Сначала настройте и подтвердите фазу кнопкой в окне фазирования.")
                return "break"
            self.batch_action()
        elif action == "skip":
            self.batch_skip()
        else:
            self.batch_step(1 if action == "next" else -1)
        return "break"

    def _refresh_batch_library(self):
        """Keep the main library in sync without rebuilding it on every 250 ms poll."""
        if not hasattr(self, "spectrum_library"):
            return
        session = self.batch_session
        signature = (
            None
            if session is None
            else (
                id(session),
                session.current_sid,
                session.paused,
                tuple(
                    (
                        item["spectrum_id"],
                        item["status"],
                        item["revision"],
                        tuple(item.get("blocking_issues", [])),
                        tuple(item.get("warnings", [])),
                        item.get("error"),
                    )
                    for item in session.items
                ),
            )
        )
        if signature != self._batch_library_signature:
            self._batch_library_signature = signature
            self.refresh_spectrum_library()

    def hide_batch_bar(self):
        self._batch_bar_hidden = True
        self.batch_bar.pack_forget()

    def open_manual_batch(self, *, new=False):
        if self._batch_running or self._spectrum_dialog_open:
            return
        if self.batch_session and not self.batch_session.finished:
            if not new:
                self._batch_bar_hidden = False
                self._refresh_batch_bar()
                return self.show_batch_queue()
            if not messagebox.askyesno(
                "Новая серия",
                "Заменить текущую очередь? Сохраните проект, если хотите продолжить её позже.",
                parent=self,
            ):
                return
        return BatchSetup(self)

    def start_manual_batch(self, ids, *, reference_id=None, formula=None, options=None):
        if self._batch_running:
            raise ValueError("Дождитесь окончания текущего вычисления.")
        if any(sid not in self.spectra for sid in ids):
            raise ValueError("В серии есть недоступный спектр.")
        if self._batch_queue_window is not None and self._batch_queue_window.winfo_exists():
            self._batch_queue_window.destroy()
        self._batch_queue_window = None
        formula = self.formula_preset_data() if formula is None else formula
        self.batch_session = BatchSession(ids, reference_id, formula=formula, options=options)
        self._batch_bar_hidden = False
        self._batch_tokens.clear()
        self._batch_diagnostic_cache.clear()
        self._batch_phase_templates.clear()
        self.batch_open_item(self.batch_session.reference_id)
        self._ensure_batch_poll()
        self._refresh_batch_bar()

    def batch_open_item(self, sid):
        if not self.batch_session or self._batch_running or self._spectrum_dialog_open:
            return
        self.stop_spin_selection()
        self.batch_session.select(sid)
        if not self.spectrum_library.accepts(sid):
            self.spectrum_library.set_batch_filter("queue")
        self.notebook.select(self.spectrum_tab)
        self.activate_spectrum(sid)
        self._refresh_batch_bar()

    def _refresh_batch_bar(self):
        self._refresh_batch_library()
        session = self.batch_session
        if session is None or not hasattr(self, "batch_bar") or self._batch_bar_hidden:
            return
        if not self.batch_bar.winfo_manager():
            self.batch_bar.pack(fill="x", after=self.zoom_button.master)
        item = session.current_item
        confirmed = sum(i["status"] == "confirmed" for i in session.items)
        name = self.spectra.get(session.current_sid, {}).get("name", "—")
        self.batch_heading.set(
            f"Серия · {session.current_index + 1}/{len(session.items)} · {name} · {STATUS_LABELS[item['status']]} · подтверждено {confirmed}"
        )
        hints = {
            "reference": "Обработайте образец: фаза, фон, обычные интегралы или Spintegrate. Затем сохраните шаблон.",
            "awaiting_phase": "Настройте фазу вручную. «Готово» в окне фазы автоматически применит фон и интегралы. Если фаза уже верна, подтвердите её кнопкой ниже.",
            "processing": "Вычисляется фон и интегралы. Фаза остаётся заданной оператором.",
            "review": "Проверьте результат. Границы обычных интегралов меняются инструментом ↔∫; модельные — через S.",
            "confirmed": "Результат подтверждён. Можно вернуться к нему или перейти дальше.",
            "skipped": "Спектр пропущен; его можно обработать позднее.",
            "error": "Ошибка: " + str(item.get("error") or "Проверьте настройки"),
        }
        hint = (
            "Очередь на паузе. Сохраните проект, чтобы продолжить позже."
            if session.paused
            else hints[item["status"]]
        )
        if item["status"] == "review" and not session.paused:
            record = self.spectra.get(session.current_sid, {})
            hint = (
                f"Фаза φ₀={record.get('phase0', 0):.3g}°, φ₁={record.get('phase1', 0):.3g}° · "
                f"шагов фона: {len(record.get('baseline_steps', []))} · интегралов: {len(record.get('integrals', []))}. "
                + hint
            )
            if item.get("blocking_issues"):
                hint = "Требует исправления: " + "; ".join(item["blocking_issues"]) + " " + hint
            elif item.get("warnings"):
                hint += f" Предупреждений: {len(item['warnings'])}; подробности — в очереди."
        if self.active_spectrum_id != session.current_sid or self.current_comparison_id is not None:
            hint = "Открыт другой график. Нажмите основную кнопку, чтобы вернуться к текущему спектру очереди."
        self.batch_hint.set(hint)
        status = item["status"]
        caption = (
            "Сохранить образец и начать →"
            if status == "reference"
            else "Подтвердить и следующий →"
            if status == "review"
            else "Следующий →"
            if status == "confirmed"
            else "Фаза готова → фон и интегралы"
        )
        self.batch_action_button.configure(
            text=caption, state="disabled" if self._batch_running or session.paused else "normal"
        )
        self.batch_phase_button.configure(
            state="disabled" if self._batch_running or session.paused else "normal"
        )
        self.batch_pause_button.configure(text="Продолжить" if session.paused else "Пауза")

    def _ensure_batch_poll(self):
        if self._batch_poll is None:
            self._batch_poll = self.after(250, self._poll_batch)

    def _poll_batch(self):
        self._batch_poll = None
        try:
            payload = self._batch_events.get_nowait()
        except queue.Empty:
            payload = None
        if payload is not None:
            session, sid, revision, before, result, diagnostic, error = payload
            self._batch_running = False
            if session is self.batch_session:
                if error:
                    session.fail_processing(sid, error, revision=revision)
                elif not same_state(self.spectra.get(sid), before):
                    session.fail_processing(
                        sid,
                        "Спектр изменился во время расчёта; результат не применён. Повторите после проверки фазы.",
                        revision=revision,
                    )
                elif session.paused or session.item(sid)["revision"] != revision:
                    pass
                elif session.complete_processing(sid, diagnostic, revision=revision):
                    previous = self.capture_records([sid])
                    self.spectra[sid] = result
                    self._undo_stack.append(
                        ("himera_records", "фон и интегралы по шаблону серии", previous)
                    )
                    self._redo_stack.clear()
                    self.trim_history()
                    self._batch_tokens[sid] = record_token(result)
                    self._batch_diagnostic_cache[sid] = (
                        record_token(result),
                        session.template_revision,
                        diagnostic,
                    )
                    if self.active_spectrum_id == sid:
                        self.restore_active_spectrum(sid)
                        self.show_integral_table()
                        self.draw()
                    self.refresh_spectrum_library()
                self._refresh_batch_bar()
                self._refresh_batch_queue()
        session = self.batch_session
        if session and not self._batch_running and not self._spectrum_dialog_open:
            changed = False
            # Undo can affect the previous spectrum after confirmation already
            # advanced the queue. Never leave such a result labelled accepted.
            for item in session.items:
                sid = item["spectrum_id"]
                if sid not in self.spectra:
                    continue
                token = record_token(self.spectra[sid])
                if (
                    item["status"] in ("review", "confirmed")
                    and sid in self._batch_tokens
                    and token != self._batch_tokens[sid]
                ):
                    self._batch_tokens[sid] = token
                    self._update_batch_review(sid)
                    changed = True
            if changed:
                self._refresh_batch_queue()
            self._refresh_batch_bar()
        if session or self._batch_running:
            self._ensure_batch_poll()

    def _update_batch_review(self, sid):
        session = self.batch_session
        item = session.item(sid)
        signature = [self.spectra[sid][key] for key in ("phase0", "phase1", "phase_pivot")]
        if (
            sid != session.reference_id
            and item.get("phase_signature") is not None
            and not same_state(signature, item["phase_signature"])
        ):
            session.phase_changed(sid)
            self._refresh_batch_bar()
            return None
        diagnostic = self._batch_diagnostic(sid)
        session.mark_review(sid, diagnostic, blocking_issues=diagnostic["blocking_issues"])
        self._refresh_batch_bar()
        return diagnostic

    def _batch_diagnostic(self, sid):
        from batch_diagnostics import inspect_batch_record

        session = self.batch_session
        token = record_token(self.spectra[sid])
        cached = self._batch_diagnostic_cache.get(sid)
        if cached and cached[:2] == (token, session.template_revision):
            return cached[2]
        diagnostic = inspect_batch_record(self.spectra[sid], session.formula)
        # Keep the accepted/replayed column set for this spectrum, not the
        # latest template (which may intentionally differ for the remaining
        # queue). Deleting an area must not turn a missing measurement into a
        # silently accepted empty cell when no formula happens to use it.
        item = session.item(sid)
        expected = item.get("diagnostic", {}).get("integrals", {})
        if sid != session.reference_id and item["status"] in ("review", "confirmed"):
            missing = sorted(set(expected) - set(diagnostic["integrals"]))
            for number in missing:
                diagnostic["integrals"][number] = None
                diagnostic["blocking_issues"].append(
                    f"I{number} из предыдущего результата отсутствует. Восстановите интеграл; "
                    "для намеренного изменения набора обновите шаблон серии."
                )
        self._batch_diagnostic_cache[sid] = (token, session.template_revision, diagnostic)
        return diagnostic

    def batch_action(self):
        session = self.batch_session
        if not session or session.paused or self._batch_running or self._spectrum_dialog_open:
            return
        if self.active_spectrum_id != session.current_sid or self.current_comparison_id is not None:
            self.batch_open_item(session.current_sid)
            return
        try:
            status = session.current_item["status"]
            if status == "reference":
                diagnostic = self._batch_diagnostic(session.current_sid)
                if diagnostic["blocking_issues"]:
                    raise ValueError("; ".join(diagnostic["blocking_issues"]))
                template = self._make_batch_template(session.formula)
                session.mark_reference_ready(template, formula=session.formula)
                self._batch_tokens[session.reference_id] = record_token(
                    self.spectra[session.reference_id]
                )
                self.batch_step(1)
            elif status == "review":
                diagnostic = self._update_batch_review(session.current_sid)
                if diagnostic is None:
                    return
                if diagnostic["blocking_issues"]:
                    raise ValueError("; ".join(diagnostic["blocking_issues"]))
                session.confirm()
                self._batch_tokens[session.current_sid] = record_token(
                    self.spectra[session.current_sid]
                )
                self.batch_step(1)
            elif status == "confirmed":
                sid = session.current_sid
                if record_token(self.spectra[sid]) != self._batch_tokens.get(sid):
                    self._batch_tokens[sid] = record_token(self.spectra[sid])
                    self._update_batch_review(sid)
                    self._refresh_batch_queue()
                    return
                self.batch_step(1)
            else:
                self.batch_phase_ready()
        except (ValueError, KeyError, RuntimeError) as error:
            messagebox.showerror("Серия", str(error), parent=self)
        self._refresh_batch_bar()
        self._refresh_batch_queue()

    def _make_batch_template(self, formula):
        record = self.spectra[self.active_spectrum_id]
        if not record.get("integrals"):
            raise ValueError("Сначала создайте интегралы на образце.")
        return make_template(
            record,
            record["integrals"],
            formula or FormulaDocument().to_preset(record["integrals"]),
            limits=sorted(self.axes.get_xlim()),
        )

    def batch_before_phase(self):
        session = self.batch_session
        sid = self.active_spectrum_id
        if (
            session
            and sid == session.current_sid
            and sid != session.reference_id
            and session.item(sid)["status"] in ("review", "confirmed")
        ):
            try:
                self._batch_phase_templates[sid] = self._make_batch_template(session.formula)
            except ValueError:
                self._batch_phase_templates.pop(sid, None)

    def on_manual_phase_committed(self, sid):
        session = self.batch_session
        if (
            not session
            or session.paused
            or session.current_sid != sid
            or sid == session.reference_id
        ):
            return
        self.batch_phase_ready()

    def on_manual_phase_cancelled(self, sid):
        self._batch_phase_templates.pop(sid, None)

    def batch_phase_ready(self):
        session = self.batch_session
        if not session or session.paused or self._batch_running or self._spectrum_dialog_open:
            return
        sid = session.current_sid
        if sid != self.active_spectrum_id or sid == session.reference_id:
            return
        if session.template is None:
            self.status.set("Сначала обработайте образец и сохраните шаблон серии.")
            return
        record = self.spectra[sid]
        session.mark_phase_done(
            sid, signature=[record["phase0"], record["phase1"], record["phase_pivot"]]
        )
        revision = session.begin_processing(sid)
        self.save_active_spectrum()
        before = copy.deepcopy(record)
        local = self._batch_phase_templates.pop(sid, None)
        recipe = local or session.template
        align = session.options.get("align", True) and local is None
        formula = session.formula
        self._batch_running = True
        self.integral_boundary_editor.set_active(False)

        def worker():
            from batch_diagnostics import inspect_batch_record

            result, diagnostic, error = None, None, None
            try:
                result, info = apply_template(
                    before, recipe, align=align, phase_mode="preserve", allow_incomplete=True
                )
                checked = inspect_batch_record(result, formula)
                diagnostic = {**info, **checked}
                diagnostic["warnings"] = list(
                    dict.fromkeys(info.get("warnings", []) + checked["warnings"])
                )
            except Exception as exc:
                error = str(exc)
            self._batch_events.put((session, sid, revision, before, result, diagnostic, error))

        threading.Thread(target=worker, daemon=True, name="himera-manual-series").start()
        self._refresh_batch_bar()
        self._ensure_batch_poll()

    def batch_step(self, direction):
        session = self.batch_session
        if not session or self._batch_running or self._spectrum_dialog_open:
            return
        if direction > 0 and session.current_index == len(session.items) - 1:
            self.show_batch_queue()
            self._refresh_batch_bar()
            return
        item = session.next() if direction > 0 else session.previous()
        self.batch_open_item(item["spectrum_id"])

    def batch_next_attention(self):
        """Open unfinished work from the left library; never apply/confirm anything."""
        session = self.batch_session
        if not session or self._batch_running or self._spectrum_dialog_open:
            return
        sid = next_attention_id(session.items, session.current_sid)
        if sid is None:
            self.status.set("Все спектры серии подтверждены.")
            return
        # The action must not select an invisible row under an incompatible
        # status filter or a search left over from the previous spectrum.
        self.spectrum_library.search.set("")
        self.spectrum_library.set_batch_filter("queue")
        self.batch_open_item(sid)
        self.spectrum_list.focus_set()

    def batch_skip(self):
        if (
            not self.batch_session
            or self.batch_session.paused
            or self._batch_running
            or self._spectrum_dialog_open
        ):
            return
        try:
            self.batch_session.skip()
            self.batch_step(1)
        except ValueError as error:
            messagebox.showinfo("Серия", str(error), parent=self)
        self._refresh_batch_queue()

    def batch_pause(self):
        if not self.batch_session:
            return
        if self.batch_session.paused:
            self.batch_session.resume()
        else:
            self.batch_session.pause()
            if self._batch_running:
                self.batch_session.retry()
        self._refresh_batch_bar()
        self._refresh_batch_queue()

    def batch_update_template(self):
        session = self.batch_session
        if not session or self._batch_running or self._spectrum_dialog_open:
            return
        if not messagebox.askyesno(
            "Обновить шаблон",
            "Применять настройки текущего спектра только к оставшимся неподтверждённым? Уже подтверждённые результаты не изменятся.",
            parent=self,
        ):
            return
        try:
            template = self._make_batch_template(session.formula)
            current = (
                copy.deepcopy(session.item(self.active_spectrum_id))
                if self.active_spectrum_id in session.spectrum_ids
                else None
            )
            session.set_template(template)
            if current:
                # This explicit action also approves the edited column set of
                # the source record; pending records use the new template.
                current["diagnostic"] = {}
                session.item(self.active_spectrum_id).update(current)
        except ValueError as error:
            messagebox.showerror("Серия", str(error), parent=self)
        self._refresh_batch_bar()
        self._refresh_batch_queue()

    def show_batch_queue(self):
        session = self.batch_session
        if not session:
            return self.open_manual_batch()
        self._batch_bar_hidden = False
        self._refresh_batch_bar()
        window = self._batch_queue_window
        if window is not None and window.winfo_exists():
            self._refresh_batch_queue()
            window.lift()
            return window
        window = ResultsWindow(self, "Himera · Очередь и результаты серии", ("Спектр", "Состояние"))
        self._batch_queue_window = window
        ttk.Label(
            window.options,
            text="Ручная фаза для каждого спектра. I — площади, независимо от способа вывода основной таблицы. "
            "Двойной щелчок — открыть для проверки.",
            wraplength=950,
        ).pack(anchor="w")
        ttk.Button(
            window.footer, text="Сохранить очередь в проекте…", command=self.save_project
        ).pack(side="left")
        ttk.Button(
            window.footer, text="Открыть выбранный спектр", command=self._batch_queue_open
        ).pack(side="left", padx=6)
        if hasattr(self, "export_integrals_csv"):
            ttk.Button(
                window.footer,
                text="Экспорт CSV…",
                command=lambda: self.export_integrals_csv(scope="batch"),
            ).pack(side="left", padx=6)
        window.table.bind("<Double-1>", lambda _event: self._batch_queue_open())
        self._refresh_batch_queue()
        return window

    def _batch_queue_open(self):
        window = self._batch_queue_window
        selected = window.table.selection() if window and window.winfo_exists() else ()
        if selected:
            self.batch_open_item(self.batch_session.items[int(selected[0])]["spectrum_id"])
            self.lift()

    def _refresh_batch_queue(self):
        session, window = self.batch_session, self._batch_queue_window
        if not session or window is None or not window.winfo_exists():
            return
        formula = session.formula
        doc = FormulaDocument.from_preset(formula) if formula else FormulaDocument()
        numbers = sorted(
            {
                i.get("number", 0)
                for sid in session.spectrum_ids
                for i in self.spectra[sid].get("integrals", [])
            }
        )
        outputs = [
            (bid, b.get("name") or f"Вывод {bid}")
            for bid, b in doc.blocks.items()
            if b.get("kind") == "output"
        ]
        headers = (
            ["Спектр", "Состояние", "Проверка"]
            + [f"I{n}" for n in numbers]
            + [name for _, name in outputs]
        )
        window.table.configure(columns=tuple(range(len(headers))))
        for index, label in enumerate(headers):
            window.table.heading(index, text=label)
            window.table.column(index, width=140, minwidth=80)
        rows = []
        for item in session.items:
            record = self.spectra[item["spectrum_id"]]
            diagnostic = (
                self._batch_diagnostic(item["spectrum_id"])
                if item["status"] in ("review", "confirmed")
                else {}
            )
            values = diagnostic.get("integrals", {})
            answers = {o["id"]: o.get("value") for o in diagnostic.get("outputs", [])}
            notes = list(diagnostic.get("blocking_issues", [])) + list(
                diagnostic.get("warnings", [])
            )
            rows.append(
                (
                    record["name"],
                    STATUS_LABELS[item["status"]],
                    item.get("error") or "; ".join(notes),
                    *[values.get(str(n)) for n in numbers],
                    *[answers.get(bid) for bid, _ in outputs],
                )
            )
        window.set_rows(rows)
        window.status.set(
            f"Подтверждено: {sum(i['status'] == 'confirmed' for i in session.items)} / {len(session.items)}. Ctrl+C — копировать таблицу. Прогресс сохраняется вместе с проектом."
        )

    def restore_batch_queue(self, payload):
        if self._batch_queue_window is not None and self._batch_queue_window.winfo_exists():
            self._batch_queue_window.destroy()
        self._batch_queue_window = None
        self._batch_tokens.clear()
        self._batch_diagnostic_cache.clear()
        self._batch_phase_templates.clear()
        self.batch_session = None
        self._batch_bar_hidden = False
        self.batch_bar.pack_forget()
        if payload is None:
            self._refresh_batch_library()
            return
        ids = list(self.spectra)
        session = BatchSession.from_payload(payload, {i: sid for i, sid in enumerate(ids)})
        session.pause()
        self.batch_session = session
        for item in session.items:
            if item["status"] in ("review", "confirmed"):
                self._batch_tokens[item["spectrum_id"]] = record_token(
                    self.spectra[item["spectrum_id"]]
                )
        self._ensure_batch_poll()
        self._refresh_batch_bar()
