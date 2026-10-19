"""On-demand, formula-independent integral stability inspection."""

import copy
import tkinter as tk
from tkinter import messagebox, ttk

from formula_engine import format_value
from integral_stability import (
    analyze_integral_stability,
    integral_stability_case_count,
    summarize_integral_stability,
)
from ui_theme import ERROR, MUTED, WARNING, mark_primary
from workflow_tools import ResultsWindow
from view_preferences import capture_table_view, restore_table_view


def open_integral_stability(app):
    if not app.workflow_available():
        return
    comparison = app.comparison_record()
    comparison_id = comparison.get("id") if comparison else None
    records = (
        [app.spectra[sid] for sid in comparison["spectrum_ids"]]
        if comparison
        else [app.spectra[app.active_spectrum_id]]
    )
    entries = []
    selected = set(app.integral_table.selection())
    selected_sids = (
        {int(iid[1:]) for iid in selected if iid.startswith("s") and iid[1:].isdigit()}
        if comparison
        else set()
    )
    inspector = getattr(app, "integral_inspector", None)
    inspector_key = getattr(inspector, "_key", None)
    selected_integral = (
        getattr(inspector, "item_id", None)
        if comparison and inspector_key and inspector_key[:2] == ("comparison", comparison_id)
        else None
    )
    if comparison and not any(
        i["id"] == selected_integral for i in comparison.get("integrals", [])
    ):
        selected_integral = None
    has_selection = (
        bool(selected_sids or selected_integral is not None) if comparison else bool(selected)
    )
    for record in records:
        items, offset = app.integral_context(record)
        if items:
            entries.append((copy.deepcopy(record), copy.deepcopy(items), offset))
    if not entries:
        messagebox.showinfo(
            "Устойчивость интегралов",
            "Сначала создайте интегралы или сохраните группы Spintegrate.",
            parent=app,
        )
        return
    window = ResultsWindow(
        app,
        "Himera · Устойчивость интегралов",
        (
            "Спектр",
            "Интеграл",
            "Площадь",
            "Мин.",
            "Макс.",
            "Макс. Δ, %",
            "Макс. Δ площади",
            "Главный сценарий",
            "Проверка",
        ),
        plot=True,
        modal=True,
    )
    window.rows, window.summaries = [], []
    window.displayed = []
    window.mode_views = {}
    window.details = False
    controls = ttk.Frame(window.options)
    controls.pack(fill="x")
    delta, sigma, percent, threshold = [
        tk.StringVar(window, value=v) for v in ("0.001", "1", "5", "5")
    ]
    for label, variable in (
        ("δ границ, ppm", delta),
        ("Фон, ×σ", sigma),
        ("Изменение линии, %", percent),
        ("Предупреждать >, %", threshold),
    ):
        ttk.Label(controls, text=label).pack(side="left", padx=(0, 4))
        ttk.Entry(controls, textvariable=variable, width=7).pack(side="left", padx=(0, 10))
    selection_row = ttk.Frame(window.options)
    selection_row.pack(fill="x", pady=(7, 0))
    only_selected = tk.BooleanVar(window, value=has_selection)
    ttk.Checkbutton(
        selection_row,
        text="Только выделенное в таблице",
        variable=only_selected,
        state="normal" if has_selection else "disabled",
    ).pack(side="left")
    ttk.Label(
        window.options,
        text="Каждый фактор меняется отдельно. Для Spintegrate меняется область подгонки и заново проверяется состав групп. "
        "NOX: масштабируется уже вычтенная линия; Spintegrate: отдельно проверяется локальная λ, если она используется.",
        wraplength=1020,
        foreground=MUTED,
    ).pack(anchor="w", pady=(6, 0))
    absolute = app.integration_method.get() == "Сумма модулей относительно 0"
    window.table.tag_configure("error", foreground=ERROR)
    window.table.tag_configure("warning", foreground=WARNING)

    def chosen_context():
        selection = window.table.selection()
        if not selection or not str(selection[0]).isdigit():
            return None
        index = int(selection[0])
        if index >= len(window.displayed):
            return None
        return window.displayed[index]

    def plot(_event=None):
        chosen = chosen_context()
        open_button.configure(state="normal" if chosen and not window.running else "disabled")
        if chosen is None:
            return
        sid, number = chosen["spectrum_id"], chosen["number"]
        summary = next(
            (s for s in window.summaries if s["spectrum_id"] == sid and s["number"] == number), None
        )
        if summary is None:
            return
        rows = [
            r
            for r in window.rows
            if r["spectrum_id"] == sid
            and r["number"] == number
            and r["status"] == "ready"
            and r["change"] is not None
            and r["case"] != "Исходный"
        ]
        factors = {}
        use_percent = summary["nominal"] not in (0, None)
        for row in rows:
            value = row["percent"] if use_percent else row["change"]
            if value is not None:
                factors[row["factor"]] = max(factors.get(row["factor"], 0), abs(value))
        ax = window.axes
        ax.clear()
        if factors:
            ax.barh(list(factors), list(factors.values()), color="#4c78a8")
            ax.invert_yaxis()
            ax.set_xlabel(
                "Максимальное |Δ|, %"
                if use_percent
                else "Максимальное |Δ площади| (исходная площадь = 0)"
            )
            ax.grid(axis="x", alpha=0.2)
        else:
            ax.text(
                0.5,
                0.5,
                "Нет пригодных сценариев для сравнения",
                ha="center",
                transform=ax.transAxes,
            )
        ax.set_title(f"{chosen['spectrum']} · I{number} · {summary['method']}", fontsize=10)
        window.figure.tight_layout()
        window.canvas.draw_idle()

    def render(preferred=None):
        chosen = preferred or chosen_context()
        key = (chosen["spectrum_id"], chosen["number"]) if chosen else None
        if window.details:
            headers = (
                "Спектр",
                "Интеграл",
                "Фактор",
                "Сценарий",
                "Площадь",
                "Δ площади",
                "Δ, %",
                "Состояние",
                "Примечание",
            )
            rows = [
                (
                    r["spectrum"],
                    f"I{r['number']} · {r['name']}",
                    r["factor"],
                    r["case"],
                    r["value"],
                    r["change"],
                    r["percent"],
                    {
                        "ready": "Готово",
                        "error": "Ошибка",
                        "skipped": "Не применяется",
                        "cancelled": "Не проверено",
                    }[r["status"]],
                    r["message"],
                )
                for r in window.rows
            ]
            tags = [
                "error" if r["status"] == "error" else "warning" if r["message"] else ""
                for r in window.rows
            ]
        else:
            headers = (
                "Спектр",
                "Интеграл",
                "Площадь",
                "Мин.",
                "Макс.",
                "Макс. Δ, %",
                "Макс. Δ площади",
                "Главный сценарий",
                "Проверка",
            )
            rows, tags = [], []
            for s in window.summaries:
                alert = s["percent"] is not None and s["percent"] > window.threshold
                note = (
                    f"Неполная проверка: {s['completed']}/{s['planned']} сценариев; ошибок {s['failed']}"
                    if s["partial"]
                    else f"Неполная проверка: ошибок {s['failed']}"
                    if s["failed"]
                    else "Исходная площадь = 0; процент не определён"
                    if s["nominal"] == 0
                    else "Изменение выше заданного порога"
                    if alert
                    else "В пределах заданного порога"
                )
                if s["notes"]:
                    note += "; " + "; ".join(s["notes"])
                rows.append(
                    (
                        s["spectrum"],
                        f"I{s['number']} · {s['name']}",
                        s["nominal"],
                        s["low"],
                        s["high"],
                        s["percent"],
                        s["change"],
                        s["worst"],
                        note,
                    )
                )
                tags.append(
                    "error"
                    if s["failed"]
                    else "warning"
                    if s["partial"] or alert or s["notes"]
                    else ""
                )
        for i, label in enumerate(headers):
            window.table.heading(i, text=label)
            window.table.column(i, width=120 if i < 7 else 240, minwidth=85)
        window.set_rows(rows)
        window.displayed = window.rows if window.details else window.summaries
        if window.details in window.mode_views:
            restore_table_view(window.table, window.mode_views[window.details])
        for index, tag in enumerate(tags):
            window.table.item(str(index), tags=(tag,) if tag else ())
        if rows:
            match = next(
                (
                    str(i)
                    for i, row in enumerate(window.displayed)
                    if (row["spectrum_id"], row["number"]) == key
                ),
                None,
            )
            if match is not None:
                window.table.selection_set(match)
                window.table.focus(match)
                window.table.see(match)
            elif not window.table.selection():
                window.table.selection_set("0")
            plot()
        else:
            window.axes.clear()
            window.axes.text(
                0.5,
                0.5,
                "Нет результатов; см. сообщение ниже",
                ha="center",
                transform=window.axes.transAxes,
            )
            window.canvas.draw_idle()

    def toggle_details():
        chosen = chosen_context()
        window.mode_views[window.details] = capture_table_view(window.table)
        window.details = not window.details
        details_button.configure(text="Показать сводку" if window.details else "Показать сценарии")
        render(preferred=chosen)

    def run():
        try:
            d, n, p, t = [
                float(var.get().replace(",", ".")) for var in (delta, sigma, percent, threshold)
            ]
            import math

            if (
                not all(math.isfinite(v) for v in (d, n, p, t))
                or d <= 0
                or not 0 <= n <= 100
                or not 0 < p <= 50
                or t <= 0
            ):
                raise ValueError("δ > 0; фон 0…100 σ; линия 0…50%; порог предупреждения > 0.")
            chosen_entries = []
            for record, items, offset in entries:
                if only_selected.get():
                    if comparison:
                        if selected_sids and record["id"] not in selected_sids:
                            continue
                        if selected_integral is not None:
                            items = [i for i in items if i["id"] == selected_integral]
                    else:
                        items = [i for i in items if str(i["id"]) in selected]
                chosen_entries.append((record, items, offset))
            chosen_entries = [entry for entry in chosen_entries if entry[1]]
            if not chosen_entries:
                raise ValueError("Нет выбранных интегралов для проверки.")
        except ValueError as error:
            window.status.set(str(error))
            return
        window.threshold = t
        run_button.configure(state="disabled")
        details_button.configure(state="disabled")
        open_button.configure(state="disabled")

        def work(stop):
            rows, errors = [], []
            total = sum(
                integral_stability_case_count(r, items) * len(items)
                for r, items, _ in chosen_entries
            )
            completed = 0

            def unavailable(record, item, planned, message, status):
                return dict(
                    spectrum=record["name"],
                    spectrum_id=record["id"],
                    number=item["number"],
                    name=item.get("name", "Интеграл"),
                    case="Исходный",
                    factor="Исходный",
                    value=None,
                    nominal=None,
                    change=None,
                    percent=None,
                    status=status,
                    message=message,
                    delta=None,
                    noise=None,
                    method="Spintegrate" if item.get("spin_fit_id") else "Численный",
                    planned_cases=planned,
                    completed_case=False,
                )

            for index, (record, items, offset) in enumerate(chosen_entries, 1):
                planned = integral_stability_case_count(record, items)
                if stop.is_set():
                    rows.extend(
                        unavailable(
                            record, item, planned, "Остановлено до проверки интеграла.", "cancelled"
                        )
                        for item in items
                    )
                    continue

                def progress(count, _planned, label):
                    window.report_progress(
                        completed + count,
                        total,
                        f"Спектр {index}/{len(chosen_entries)} · {record['name']} · {label}",
                    )

                try:
                    result = analyze_integral_stability(
                        record,
                        items,
                        offset=offset,
                        delta=d,
                        background_sigma=n,
                        baseline_percent=p,
                        absolute=absolute,
                        stop=stop,
                        progress=progress,
                        keep_partial=True,
                    )
                    rows.extend(
                        dict(row, spectrum=record["name"], spectrum_id=record["id"])
                        for row in result
                    )
                    checked = {row["number"] for row in result}
                    if stop.is_set():
                        rows.extend(
                            unavailable(
                                record,
                                item,
                                planned,
                                "Остановлено до проверки интеграла.",
                                "cancelled",
                            )
                            for item in items
                            if item["number"] not in checked
                        )
                except Exception as error:
                    errors.append(f"{record['name']}: {error}")
                    # Keep failed spectra visible in the table as unavailable
                    # measurements, not silently missing rows of a comparison.
                    rows.extend(
                        unavailable(record, item, planned, str(error), "error") for item in items
                    )
                completed += planned * len(items)
            return rows, errors

        def done(payload):
            window.rows, errors = payload
            window.summaries = []
            for record, _, _ in chosen_entries:
                subset = [row for row in window.rows if row["spectrum_id"] == record["id"]]
                window.summaries.extend(
                    dict(s, spectrum=record["name"], spectrum_id=record["id"])
                    for s in summarize_integral_stability(subset)
                )
            run_button.configure(state="normal")
            details_button.configure(state="normal")
            render()
            actual = sorted({row["delta"] for row in window.rows if row["delta"] is not None})
            noises = [
                f"{record['name']}: {format_value(next((r['noise'] for r in window.rows if r['spectrum_id'] == record['id']), None))}"
                for record, _, _ in chosen_entries
            ]
            window.status.set(
                f"Интегралов: {len(window.summaries)}. Завершено сценариев: {sum(s['completed'] for s in window.summaries)}/{sum(s['planned'] for s in window.summaries)}. "
                + ("Проверка неполная. " if any(s["partial"] for s in window.summaries) else "")
                + f"Фактическое δ: {', '.join(format_value(v) for v in actual)} ppm (не меньше шага сетки). "
                f"σ по разностям точек: {'; '.join(noises[:4])}. "
                "Площади, не суммы точек. Диапазон только по успешным сценариям; ошибки не заменяются нулями. "
                "Это чувствительность к заданным изменениям, а не погрешность и не доверительный интервал. "
                + " ".join(errors[:3])
                + (
                    f" Ещё ошибок спектров: {len(errors) - 3}; подробности — в таблице."
                    if len(errors) > 3
                    else ""
                )
            )

        window.start(work, done)

    def open_selected():
        chosen = chosen_context()
        if chosen is None or window.running:
            return
        sid, number = chosen["spectrum_id"], chosen["number"]
        window.destroy()  # release the modal guard before selecting another record
        app.focus_integral(sid, number, comparison_id=comparison_id)

    window.table.bind("<<TreeviewSelect>>", plot)
    run_button = mark_primary(tk.Button(selection_row, text="Проверить устойчивость", command=run))
    run_button.pack(side="right")
    details_button = ttk.Button(
        window.footer, text="Показать сценарии", command=toggle_details, state="disabled"
    )
    details_button.pack(side="left")
    open_button = ttk.Button(
        window.footer, text="Открыть интеграл", command=open_selected, state="disabled"
    )
    open_button.pack(side="left", padx=6)
    window.status.set(
        "Снимок данных на момент открытия. Исходные спектры, фаза, фон и интегралы не меняются. "
        "Для Spintegrate проверка медленнее: выполняются повторные подгонки. «Остановить» сохраняет готовые сценарии. Ctrl+C — копировать."
    )
    window.stability_run = run
    window.stability_toggle_details = toggle_details
    window.stability_open_selected = open_selected
    return window
