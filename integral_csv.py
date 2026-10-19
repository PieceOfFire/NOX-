"""Explicit-unit integration tables for CSV; no Tk and no record mutations."""

import csv
import math
from pathlib import Path

import numpy as np

from integration import integrate_region
from sensitivity import area_for_context
from spin_backend import SPIN_DISPLAY, current_signal


DISCRETE = "Сумма дискретных точек (MestReNova)"
DISPLAYS = ("Площадь", DISCRETE, SPIN_DISPLAY)


def integration_value(record, item, *, offset=0.0, absolute=False, display="Площадь"):
    if display not in DISPLAYS:
        raise ValueError("Неизвестные единицы экспорта.")
    if not math.isfinite(float(offset)):
        raise ValueError("Смещение X должно быть конечным.")
    if item.get("spin_fit_id"):
        value = area_for_context(record, item, offset, absolute)
        if display == SPIN_DISPLAY:
            value /= np.pi
        elif display == DISCRETE:
            step = abs(float(np.median(np.diff(record["ppm"]))))
            if not math.isfinite(step) or step <= 0:
                raise ValueError("Неверный шаг шкалы ppm.")
            value /= step
    else:
        x = np.asarray(record["ppm"], dtype=float)
        a, b = float(item["left"]) - offset, float(item["right"]) - offset
        if not np.isfinite([a, b]).all() or a >= b or a < x.min() or b > x.max():
            raise ValueError("Область вне спектра или неверные границы.")
        if np.count_nonzero((x >= a) & (x <= b)) < 3:
            raise ValueError("В области меньше трёх точек.")
        value = integrate_region(
            x, current_signal(record), a, b, absolute=absolute, discrete=display == DISCRETE
        )
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Подгонка/состав недоступны или устарели.")
    return value


def build_integration_table(entries, *, display="Площадь", include_bounds=False):
    """Rows=spectra, columns=integral numbers; absent values stay None.

    Each entry supplies record/items/offset/method and optional queue status.
    Names/limits may differ between spectra; optional metadata preserves them.
    """
    if display not in DISPLAYS:
        raise ValueError("Неизвестные единицы экспорта.")
    numbers = sorted({int(i["number"]) for e in entries for i in e["items"]})
    if not numbers:
        raise ValueError("Нет интегралов для экспорта.")
    unit = {
        "Площадь": "Интенсивность × ppm",
        DISCRETE: "Сумма дискретных точек",
        SPIN_DISPLAY: "Численные: площадь; модельные: площадь / π",
    }[display]
    headers = ["Спектр", "Состояние", "Метод", "Единицы"]
    for number in numbers:
        headers.append(f"I{number}")
        if include_bounds:
            headers.extend((f"I{number}: имя", f"I{number}: от, ppm", f"I{number}: до, ppm"))
    headers.append("Примечания")
    rows, warnings = [], 0
    for entry in entries:
        record, items = entry["record"], entry["items"]
        offset = float(entry.get("offset", 0))
        method = entry.get("method", record.get("integration_method", "Обычная площадь"))
        row = [record["name"], entry.get("status", ""), method, unit]
        notes = []
        for number in numbers:
            matches = [i for i in items if i.get("number") == number]
            item = matches[0] if len(matches) == 1 else None
            value = None
            try:
                if not item:
                    raise ValueError("нет интеграла" if not matches else "номер повторяется")
                value = integration_value(
                    record,
                    item,
                    offset=offset,
                    absolute=method == "Сумма модулей относительно 0",
                    display=display,
                )
            except (
                ValueError,
                TypeError,
                KeyError,
                IndexError,
                ArithmeticError,
                RuntimeError,
            ) as error:
                notes.append(f"I{number}: {error}")
                warnings += 1
            row.append(value)
            if include_bounds:
                row.extend(
                    (item.get("name", f"I{number}"), item.get("left"), item.get("right"))
                    if item
                    else (None, None, None)
                )
        row.append("; ".join(notes))
        rows.append(row)
    return headers, rows, warnings


def write_integration_csv(path, headers, rows, *, separator=";", decimal_comma=True):
    """UTF-8 BOM works with Windows Excel; text cells cannot execute formulas."""
    if separator not in (";", ",", "\t"):
        raise ValueError("Разделитель: точка с запятой, запятая или табуляция.")

    def cell(value):
        if value is None:
            return ""
        if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                return ""
            result = format(value, ".17g")
            return result.replace(".", ",") if decimal_comma else result
        result = str(value)
        if result.lstrip().startswith(("=", "+", "-", "@")):
            result = "'" + result
        return result

    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=separator, lineterminator="\r\n")
        writer.writerow([cell(h) for h in headers])
        writer.writerows([[cell(v) for v in row] for row in rows])
