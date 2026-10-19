"""Readable spectrum labels and a searchable, status-aware library widget.

Bruker titles are labels only: they never replace stable record names used by
presets, and are never interpreted as markup or executable instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import ttk


BATCH_STATUS_LABELS = {
    "reference": "Образец · настройте обработку",
    "awaiting_phase": "Нужна ручная фаза",
    "processing": "Фон и интегралы…",
    "review": "Проверить результат",
    "confirmed": "Подтверждён",
    "skipped": "Пропущен",
    "error": "Ошибка",
}
BATCH_FILTER_LABELS = {
    "all": "Все спектры",
    "queue": "Вся серия",
    "waiting": "Ждут обработки",
    "review": "Нужна проверка",
    "errors": "Ошибки / исправления",
    "skipped": "Пропущены",
    "confirmed": "Подтверждены",
}


def matches_batch_filter(item: dict | None, key: str) -> bool:
    """Queue filters never infer scientific acceptance from the plotted spectrum."""
    if key == "all":
        return True
    if item is None:
        return False
    status = item.get("status")
    if key == "queue":
        return True
    if key == "waiting":
        return status in {"reference", "awaiting_phase", "processing"}
    if key == "review":
        return status == "review"
    if key == "errors":
        return status == "error" or bool(item.get("blocking_issues"))
    return status == {"skipped": "skipped", "confirmed": "confirmed"}.get(key)


def next_attention_id(items: list[dict], current_sid: int | None) -> int | None:
    """Next unfinished item in queue order, wrapping once; skipped items remain reachable."""
    ids = [item["spectrum_id"] for item in items]
    start = ids.index(current_sid) + 1 if current_sid in ids else 0
    ordered = items[start:] + items[:start]
    return next(
        (
            item["spectrum_id"]
            for item in ordered
            if item.get("status") not in {"confirmed", "processing"}
        ),
        None,
    )


def read_bruker_title(source_folder: str | Path) -> str:
    """Read the selected processed spectrum's title, including older Cyrillic files."""
    try:
        path = Path(source_folder) / "title"
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536 or b"\0" in raw:
            return ""
    except (OSError, ValueError, TypeError):
        return ""
    for encoding in ("utf-8-sig", "cp1251", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return ""
    return clean_title(text)


def clean_title(value: object) -> str:
    """Keep bounded plain text suitable for both a saved project and Tk labels."""
    if not isinstance(value, str):
        return ""
    lines = value.replace("\r", "\n").splitlines()
    lines = [
        " ".join("".join(c for c in line if c.isprintable() or c == "\t").split()) for line in lines
    ]
    return "\n".join(line for line in lines if line)[:4096]


def spectrum_label(record: dict) -> str:
    name = str(record.get("name", "Спектр"))
    title = clean_title(record.get("bruker_title", ""))
    first_line = title.splitlines()[0] if title else ""
    return f"{name} · {first_line}" if first_line and first_line != name else name


def matches_search(record: dict, query: str) -> bool:
    haystack = " ".join(
        str(record.get(key, "")) for key in ("name", "bruker_title", "source_folder")
    )
    return all(word in haystack.casefold() for word in query.casefold().split())


@dataclass(frozen=True)
class LibraryStatus:
    summary: str
    phase_changed: bool
    baseline_applied: bool
    fit_state: str
    issues: tuple[str, ...]


def library_status(record: dict) -> LibraryStatus:
    """Describe recorded processing, never claim that a phase/fit is scientifically correct."""
    from spin_backend import fit_current

    phase = any(float(record.get(key, 0.0) or 0.0) != 0.0 for key in ("phase0", "phase1"))
    baseline = record.get("corrected") is not None
    fits = record.get("spin_analyses", {})
    items = [item for item in record.get("integrals", []) if item.get("spin_fit_id")]
    fit_ids = {item["spin_fit_id"] for item in items}
    if record.get("last_spin_fit"):
        fit_ids.add(record["last_spin_fit"])
    issues, stale, missing = [], False, False
    for fit_id in sorted(fit_ids):
        state = fits.get(fit_id)
        if not state:
            missing = True
            issues.append("Не найдена сохранённая подгонка; откройте область Spintegrate заново.")
            continue
        if not fit_current({**record, "spin": state}):
            stale = True
            issues.append("Спектр изменён после подгонки; пересчитайте область Spintegrate.")
        groups = {group.get("id"): group for group in state.get("groups", [])}
        selected = [item for item in items if item["spin_fit_id"] == fit_id]
        groups_to_check = [
            (str(item.get("name", "Интеграл")), groups.get(item.get("spin_group_id")))
            for item in selected
            if item.get("spin_group_id")
        ]
        if not selected:
            groups_to_check = [
                (str(group.get("name", "Интеграл")), group) for group in groups.values()
            ]
        count = len(state.get("models", [{}])[0].get("centers", [])) if state.get("models") else 0
        for name, group in groups_to_check:
            invalid = (
                group is None
                or group.get("missing", 0)
                or any(
                    type(index) is not int or not 0 <= index < count
                    for index in group.get("components", [])
                )
            )
            if invalid:
                missing = True
                issues.append(f"{name}: потеряны линии; проверьте состав интеграла в Spintegrate.")
    fit_state = "missing" if missing else "stale" if stale else "current" if fit_ids else "absent"
    labels = [label for flag, label in ((phase, "Фаза"), (baseline, "Фон")) if flag]
    if fit_state != "absent":
        labels.append(
            {"current": "Подгонка ✓", "stale": "Пересчитать", "missing": "Проверить состав"}[
                fit_state
            ]
        )
    return LibraryStatus(
        " · ".join(labels) or "Исходный", phase, baseline, fit_state, tuple(dict.fromkeys(issues))
    )


def spectrum_details(record: dict, status: LibraryStatus | None = None) -> str:
    status = status or library_status(record)
    lines = [str(record.get("name", "Спектр"))]
    title = clean_title(record.get("bruker_title", ""))
    if title:
        lines.append(title)
    lines.append(
        f"Фаза: φ₀ {float(record.get('phase0', 0.0)):.4g}°, φ₁ {float(record.get('phase1', 0.0)):.4g}°"
        + (" (изменена)" if status.phase_changed else " (без изменений)")
    )
    lines.append("Фон NOX: вычтен" if status.baseline_applied else "Фон NOX: не вычитался")
    lines.extend(status.issues)
    if record.get("source_folder"):
        lines.append(str(record["source_folder"]))
    return "\n".join(lines)


class SpectrumList(ttk.Treeview):
    """Tree with readable status column and the former Listbox selection API."""

    def __init__(self, master, **kwargs):
        super().__init__(
            master, columns=("status",), show="tree headings", selectmode="browse", **kwargs
        )
        self.heading("#0", text="Спектр / название Bruker", anchor="w")
        self.heading("status", text="Обработка", anchor="w")
        self.column("#0", minwidth=110, width=195, stretch=True)
        self.column("status", minwidth=90, width=130, stretch=False)
        self.tag_configure("problem", foreground="#8a5a00")
        self.tag_configure("section", foreground="#667085")
        self.tag_configure("confirmed", foreground="#246b49")
        self.tag_configure("review", foreground="#245b93")
        self.tag_configure("waiting", foreground="#667085")
        self._rows = ()

    def set_rows(self, rows):
        """Update only changed content; redraws must not scroll or steal selection/focus."""
        rows = tuple(rows)
        if rows == self._rows:
            return
        scroll = self.yview()[0]
        children = self.get_children()
        if children:
            super().delete(*children)
        for index, (label, status, tag) in enumerate(rows):
            super().insert(
                "", "end", iid=str(index), text=label, values=(status,), tags=(tag,) if tag else ()
            )
        self._rows = rows
        self.yview_moveto(scroll)

    def curselection(self):
        return tuple(int(iid) for iid in self.selection())

    def selection_set(self, first, last=None):
        if isinstance(first, int):
            iid = str(first)
            if self.exists(iid):
                super().selection_set(iid)
            return
        super().selection_set(first)

    def selection_clear(self, first=0, last="end"):
        super().selection_remove(self.selection())

    def activate(self, index):
        if self.exists(str(index)):
            self.focus(str(index))

    def get(self, index):
        return self.item(str(index), "text")

    def size(self):
        return len(self.get_children())

    def set_drop_active(self, active):
        # The visible textual hint is not dependent on a platform-specific theme colour.
        self.heading(
            "#0", text="Отпустите, чтобы добавить" if active else "Спектр / название Bruker"
        )


class SpectrumLibrary(ttk.Frame):
    def __init__(self, master, on_search, on_selection):
        super().__init__(master, padding=(4, 2))
        self._on_search = on_search
        self._on_next = None
        self._batch_session = None
        self._batch_items = {}
        self._batch_signature = None
        self._filter_key = "all"
        self.search = tk.StringVar(self, value="")
        search_row = ttk.Frame(self)
        search_row.pack(fill="x", pady=(0, 6))
        ttk.Label(search_row, text="Поиск").pack(side="left", padx=(0, 5))
        self.search_entry = ttk.Entry(search_row, textvariable=self.search, width=16)
        self.search_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(search_row, text="×", width=2, command=lambda: self.search.set("")).pack(
            side="right", padx=(3, 0)
        )
        self.search_entry.bind("<Escape>", lambda _event: self.search.set(""))
        self.batch_controls = ttk.Frame(self)
        self.batch_progress = tk.StringVar(self)
        ttk.Label(self.batch_controls, textvariable=self.batch_progress).pack(anchor="w")
        self.batch_filter = ttk.Combobox(self.batch_controls, state="readonly", width=24)
        self.batch_filter.pack(fill="x", pady=(4, 4))
        self.batch_filter.bind("<<ComboboxSelected>>", self._filter_changed)
        self.next_attention_button = ttk.Button(
            self.batch_controls, text="Следующий требующий внимания →", command=self._next_attention
        )
        self.next_attention_button.pack(fill="x", pady=(0, 6))
        self.count = tk.StringVar(self, value="Нет добавленных спектров")
        self.count_label = ttk.Label(self, textvariable=self.count)
        self.count_label.pack(fill="x", pady=(0, 5))
        tree_body = ttk.Frame(self)
        tree_body.pack(fill="both", expand=True)
        self.tree = SpectrumList(tree_body)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(tree_body, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.detail = tk.StringVar(
            self,
            value="Добавьте папки или перетащите их сюда.\nНазвание берётся из Bruker title; имя спектра сохраняется.",
        )
        self._full_details = self.detail.get()
        detail_body = ttk.Frame(self, height=145)
        detail_body.pack(fill="x", pady=(8, 2))
        detail_body.pack_propagate(False)
        self.detail_label = ttk.Label(
            detail_body, textvariable=self.detail, justify="left", anchor="nw", wraplength=310
        )
        self.detail_label.pack(fill="both", expand=True)
        ttk.Button(self, text="Сведения о спектре…", command=self.show_details).pack(fill="x")
        self.bind(
            "<Configure>",
            lambda event: self.detail_label.configure(wraplength=max(140, event.width - 10)),
        )
        self.tree.bind("<<TreeviewSelect>>", on_selection)
        self.search.trace_add("write", lambda *_args: on_search())

    @property
    def filter_key(self):
        return self._filter_key

    @property
    def show_comparisons(self):
        return self._filter_key == "all"

    def update_batch(self, session, on_next=None):
        """Refresh queue badges and counts without changing search, focus or selection.

        Call before building the library rows. This method does not invoke the
        search callback, so it is safe inside ``refresh_spectrum_library``.
        """
        self._on_next = on_next
        if session is None:
            self._batch_session = None
            self._batch_items = {}
            self._batch_signature = None
            self._filter_key = "all"
            self.batch_controls.pack_forget()
            return
        signature = (
            id(session),
            session.current_sid,
            session.paused,
            tuple(
                (
                    item["spectrum_id"],
                    item["status"],
                    bool(item.get("blocking_issues")),
                    tuple(item.get("warnings", [])),
                    item.get("error"),
                )
                for item in session.items
            ),
        )
        if self._batch_session is not session:
            self._filter_key = "queue"
        self._batch_session = session
        self._batch_items = {item["spectrum_id"]: item for item in session.items}
        if not self.batch_controls.winfo_manager():
            self.batch_controls.pack(fill="x", before=self.count_label)
        if signature == self._batch_signature:
            return
        self._batch_signature = signature
        counts = {
            key: sum(matches_batch_filter(item, key) for item in session.items)
            for key in BATCH_FILTER_LABELS
        }
        self.batch_filter.configure(
            values=[
                label if key == "all" else f"{label} ({counts[key]})"
                for key, label in BATCH_FILTER_LABELS.items()
            ]
        )
        self.batch_filter.current(list(BATCH_FILTER_LABELS).index(self._filter_key))
        total = len(session.items)
        self.batch_progress.set(
            f"Серия · подтверждено {counts['confirmed']} / {total}"
            + (" · пауза" if session.paused else "")
        )
        self.next_attention_button.configure(
            state="normal"
            if on_next
            and next_attention_id(session.items, session.current_sid) is not None
            and not any(item["status"] == "processing" for item in session.items)
            else "disabled"
        )

    def set_batch_filter(self, key):
        """Select a filter by its stable key (also used by navigation actions/tests)."""
        if key not in BATCH_FILTER_LABELS:
            raise ValueError("Неизвестный фильтр серии.")
        self._filter_key = key if self._batch_session else "all"
        self.batch_filter.current(list(BATCH_FILTER_LABELS).index(self._filter_key))
        self._on_search()

    def _filter_changed(self, _event=None):
        index = self.batch_filter.current()
        if index >= 0:
            self.set_batch_filter(list(BATCH_FILTER_LABELS)[index])

    def _next_attention(self):
        if self._on_next:
            self._on_next()

    def accepts(self, spectrum_id):
        return matches_batch_filter(self._batch_items.get(spectrum_id), self._filter_key)

    def decorate(self, spectrum_id, summary, problem=False):
        item = self._batch_items.get(spectrum_id)
        if item is None:
            return summary, "problem" if problem else ""
        status = item["status"]
        label = BATCH_STATUS_LABELS.get(status, status)
        if spectrum_id == self._batch_session.current_sid:
            label = "▶ " + label
        if status == "error" or item.get("blocking_issues") or problem:
            tag = "problem"
        elif status == "confirmed":
            tag = "confirmed"
        elif status == "review":
            tag = "review"
        else:
            tag = "waiting"
        return label, tag

    def batch_details(self, spectrum_id):
        item = self._batch_items.get(spectrum_id)
        if item is None:
            return ""
        notes = ["Серия: " + BATCH_STATUS_LABELS.get(item["status"], item["status"])]
        if item.get("error"):
            notes.append(str(item["error"]))
        notes.extend(item.get("blocking_issues", []))
        notes.extend(item.get("warnings", []))
        return "\n".join(dict.fromkeys(notes))

    def set_details(self, text):
        self._full_details = text
        # A large acquisition note or many stale fits must not squeeze the list away.
        compact = "\n".join(
            line[:160] + ("…" if len(line) > 160 else "") for line in text.splitlines()[:7]
        )
        self.detail.set(compact)

    def show_details(self):
        window = tk.Toplevel(self)
        window.title("Сведения о спектре")
        window.geometry("660x390")
        window.minsize(420, 250)
        window.transient(self.winfo_toplevel())
        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        text = tk.Text(body, wrap="word", padx=10, pady=10, relief="flat", height=10)
        text.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        scrollbar.pack(side="right", fill="y")
        text.configure(yscrollcommand=scrollbar.set)
        text.insert("1.0", self._full_details)
        text.configure(state="disabled")
        ttk.Button(window, text="Закрыть", command=window.destroy).pack(
            anchor="e", padx=12, pady=(0, 12)
        )
        window.bind("<Escape>", lambda _event: window.destroy())
