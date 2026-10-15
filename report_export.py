"""Self-contained, printable HTML reports with escaped text and embedded spectrum plots."""

from __future__ import annotations

from datetime import datetime
from html import escape
from io import StringIO
import json
from pathlib import Path

from matplotlib.figure import Figure
import numpy as np

from formula_engine import format_value
from spin_backend import current_signal, fit_current, model_component_areas
from signal_quality import fit_quality


def table(headers, rows):
    def cell(value):
        if value is None:
            return "—"
        if isinstance(value, (float, np.floating)):
            value = format_value(value) if np.isfinite(value) else "—"
        return escape(str(value))

    return (
        "<table><thead><tr>"
        + "".join(f"<th>{cell(h)}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{cell(v)}</td>" for v in row) + "</tr>" for row in rows)
        + "</tbody></table>"
    )


def spectrum_svg(record, items, offset):
    fig = Figure(figsize=(10, 3.5), dpi=100)
    ax = fig.subplots()
    x, y = np.asarray(record["ppm"]) + offset, current_signal(record)
    ax.plot(x, y, lw=0.7, color="#4c78a8")
    for item in items:
        ax.axvspan(item["left"], item["right"], color=item["color"], alpha=0.12)
        ax.text(
            (item["left"] + item["right"]) / 2,
            0.95,
            str(item["number"]),
            transform=ax.get_xaxis_transform(),
            ha="center",
        )
    ax.set_xlim(float(np.max(x)), float(np.min(x)))
    ax.set_xlabel("Химический сдвиг, ppm")
    ax.set_ylabel("Интенсивность")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    stream = StringIO()
    fig.savefig(stream, format="svg")
    svg = stream.getvalue()
    return svg[svg.index("<svg") :]


def fit_svg(model, shift, noise):
    figure = Figure(figsize=(10, 4), dpi=100)
    top, residual = figure.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    x = model["x"] + shift
    top.plot(x, model["corrected"], lw=0.8, label="Сигнал после локальной линии")
    top.plot(x, model["fitted"], "--", lw=1, label="Подгонка")
    residual.plot(x, model["corrected"] - model["fitted"], lw=0.7, color="#666")
    residual.axhline(0, color="#999", lw=0.5)
    residual.axhspan(-2 * noise, 2 * noise, alpha=0.12, color="#59a14f")
    residual.set_ylabel("Остаток")
    residual.set_xlabel("Химический сдвиг, ppm")
    top.set_xlim(float(np.max(x)), float(np.min(x)))
    top.legend(fontsize=8)
    for ax in (top, residual):
        ax.grid(alpha=0.2)
    figure.tight_layout()
    stream = StringIO()
    figure.savefig(stream, format="svg")
    svg = stream.getvalue()
    return svg[svg.index("<svg") :]


def write_report(path, entries, formula, sensitivity=None, stop=None):
    sections = []
    for entry in entries:
        if stop is not None and stop.is_set():
            return False
        record = entry["record"]
        sections.append(f"<section><h2>{escape(str(record['name']))}</h2>")
        sections.append(f"<p>Источник: {escape(str(record['source_folder']))}</p>")
        sections.append(
            table(
                [
                    "φ₀, °",
                    "φ₁, °",
                    "Pivot",
                    "Сдвиг шкалы, ppm",
                    "Сдвиг сравнения, ppm",
                    "Интегрирование",
                    "Вывод",
                ],
                [
                    [
                        record["phase0"],
                        record["phase1"],
                        record["phase_pivot"],
                        record.get("ppm_shift", 0),
                        entry["offset"],
                        entry["method"],
                        entry["display"],
                    ]
                ],
            )
        )
        sections.append(spectrum_svg(record, entry["items"], entry["offset"]))
        sections.append(
            table(
                [
                    "Интеграл",
                    "Название",
                    "Пределы, ppm",
                    "Площадь, интенсивность×ppm",
                    "Значение таблицы",
                    "Тип",
                    "Диагностика",
                ],
                entry["integrals"],
            )
        )
        sections.append(table(["Вывод", "Результат", "Состояние"], entry["outputs"]))
        if entry.get("formula_inputs"):
            sections.append(
                table(
                    [
                        "Блок",
                        "Вход расчёта",
                        "Пределы, ppm",
                        "Сдвиг сравнения, ppm",
                        "Физическая площадь",
                    ],
                    entry["formula_inputs"],
                )
            )
        for fid in {i.get("spin_fit_id") for i in entry["items"] if i.get("spin_fit_id")}:
            state = record.get("spin_analyses", {}).get(fid)
            if not state:
                continue
            model = state["models"][0]
            quality = fit_quality(model)
            sections.append("<h3>Spintegrate</h3>")
            current = fit_current({**record, "spin": state})
            shift = (
                float(record.get("ppm_shift", 0))
                - float(state["expected"].get("ppm_shift", 0))
                + entry["offset"]
            )
            sections.append(fit_svg(model, shift, quality["noise"]))
            sections.append(
                table(
                    ["RMSE", "Шум σ", "RMSE/σ", "Корреляция остатка", "R², %", "Состояние"],
                    [
                        [
                            quality["rmse"],
                            quality["noise"],
                            quality["noise_ratio"],
                            quality["autocorrelation"],
                            None if quality["r_squared"] is None else 100 * quality["r_squared"],
                            "; ".join(
                                quality["warnings"] + ([] if current else ["Подгонка устарела"])
                            )
                            or "Готово",
                        ]
                    ],
                )
            )
            memberships = {i: g["name"] for g in state.get("groups", []) for i in g["components"]}
            sections.append(
                table(
                    ["Центр, ppm", "FWHM, Гц", "Полная площадь", "Группа"],
                    [
                        [
                            c + shift,
                            2 * w * state["sf_mhz"],
                            model_component_areas(model)[i],
                            memberships.get(i, "Не назначена"),
                        ]
                        for i, (c, a, w) in enumerate(
                            zip(model["centers"], model["amplitudes"], model["widths"])
                        )
                    ],
                )
            )
            sections.append(
                "<details><summary>Параметры подгонки</summary><pre>"
                + escape(json.dumps(state["recipe"], ensure_ascii=False, indent=2))
                + "</pre></details>"
            )
        if record.get("corrected") is not None and (
            not record.get("baseline_steps") or record.get("baseline_source_unknown")
        ):
            sections.append(
                "<p>Сохранённая коррекция базовой линии не содержит полного рецепта; её параметры нельзя восстановить из этого проекта.</p>"
            )
        sections.append(
            "<details><summary>Шаги коррекции базовой линии</summary><pre>"
            + escape(json.dumps(record.get("baseline_steps", []), ensure_ascii=False, indent=2))
            + "</pre></details></section>"
        )
    if sensitivity:
        sections.append(
            "<h2>Сохранённая оценка устойчивости</h2><p>Это чувствительность к выбранным изменениям, а не доверительный интервал. Результаты относятся к состоянию на момент анализа.</p>"
        )
        sections.append(
            table(
                ["Спектр", "Сценарий", "Вывод", "Значение", "Состояние"],
                [
                    [r["spectrum"], r["case"], r["name"], r["value"], r["message"]]
                    for r in sensitivity
                ],
            )
        )
    sections.append(
        "<details><summary>Формула для воспроизведения</summary><pre>"
        + escape(json.dumps(formula, ensure_ascii=False, indent=2))
        + "</pre></details>"
    )
    html = "<!doctype html><html lang='ru'><meta charset='utf-8'><title>Himera · Отчёт</title><style>body{font:14px system-ui,sans-serif;color:#202020;max-width:1200px;margin:24px auto;padding:0 20px}table{border-collapse:collapse;width:100%;margin:12px 0;font-size:12px}td,th{border:1px solid #bbb;padding:7px;text-align:left}th{background:#f0f0f0}svg{width:100%;height:auto}section{margin:30px 0;break-inside:avoid}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media print{details{display:block}details>pre{display:block}body{margin:0}}</style><h1>Himera · Отчёт обработки</h1>"
    html += f"<p>Создан: {datetime.now().strftime('%d.%m.%Y %H:%M')}</p>"
    html += (
        "<p>Фон у границ — диагностическая оценка: боковые полосы могут содержать хвосты соседних сигналов. Площадь и сумма точек имеют разные единицы.</p>"
        + "".join(sections)
        + "</html>"
    )
    if stop is not None and stop.is_set():
        return False
    Path(path).write_text(html, encoding="utf-8")
    return True
