"""One-factor-at-a-time checks on detached data, not measurement uncertainty."""

from __future__ import annotations

import copy
import math

import numpy as np

from base_line import phase_correct
from sensitivity import area_for_context
from signal_quality import noise_sigma
from spin_backend import current_signal, fit_current, run_selected_region
from spin_groups import remap_groups


LAMBDA_METHODS = {"asls", "iasls", "arpls", "airpls", "drpls"}


def _finite(value):
    return value is not None and math.isfinite(float(value))


def integral_stability_case_count(record, items):
    """Planned scenarios per integral, including inapplicable local-λ cases."""
    states = record.get("spin_analyses", {})
    has_lambda = any(
        states.get(item.get("spin_fit_id"), {})
        .get("recipe", {})
        .get("baseline_default", {})
        .get("method")
        in LAMBDA_METHODS
        for item in items
    )
    return 13 + 2 * (record.get("corrected") is not None) + 2 * has_lambda


def analyze_integral_stability(
    record,
    items,
    *,
    offset=0.0,
    delta=0.001,
    background_sigma=1.0,
    baseline_percent=5.0,
    absolute=False,
    stop=None,
    progress=None,
    keep_partial=False,
):
    """Re-evaluate areas, varying one factor at a time; input is never modified.

    Background offsets/slopes use a difference-based noise estimate. NOX tests
    rescale the actually subtracted curve, not an algorithm's hidden parameters.
    Spintegrate tests alter its real fit domain and, when applicable, local λ;
    selected groups retain identities, not arbitrary replacement peak indices.
    """
    if not all(np.isfinite(v) for v in (offset, delta, background_sigma, baseline_percent)):
        raise ValueError("Параметры должны быть конечными числами.")
    if delta <= 0 or not 0 <= background_sigma <= 100 or not 0 < baseline_percent <= 50:
        raise ValueError("δ > 0; фон от 0 до 100 σ; изменение линии от 0 до 50%.")
    if not items:
        raise ValueError("Нет интегралов для проверки.")
    x = np.asarray(record["ppm"], dtype=float)
    signal = np.asarray(current_signal(record), dtype=float)
    if (
        x.ndim != 1
        or len(x) < 3
        or x.shape != signal.shape
        or not np.isfinite(x).all()
        or not np.isfinite(signal).all()
        or not (np.all(np.diff(x) > 0) or np.all(np.diff(x) < 0))
    ):
        raise ValueError("Некорректные данные спектра.")
    items = copy.deepcopy(items)
    numbers = [i["number"] for i in items]
    if len(set(numbers)) != len(numbers):
        raise ValueError("Номера интегралов повторяются.")
    step = abs(float(np.median(np.diff(x))))
    d = max(float(delta), step)
    noise = noise_sigma(signal)
    floor = noise * background_sigma
    fraction = baseline_percent / 100
    # case, factor, left change, right change, offset, slope, NOX strength, local λ
    cases = [("Исходный", "Исходный", 0, 0, 0, 0, 1, 1)]
    for label, left, right in (
        ("Область шире", -d, d),
        ("Область уже", d, -d),
        ("Левая граница −δ", -d, 0),
        ("Левая граница +δ", d, 0),
        ("Правая граница −δ", 0, -d),
        ("Правая граница +δ", 0, d),
        ("Область сдвинута −δ", -d, -d),
        ("Область сдвинута +δ", d, d),
    ):
        cases.append((label, "Границы", left, right, 0, 0, 1, 1))
    for sign in (-1, 1):
        suffix = f"{'+' if sign > 0 else '−'}{background_sigma:g}σ"
        cases.append((f"Уровень фона {suffix}", "Фон", 0, 0, sign * floor, 0, 1, 1))
        cases.append((f"Наклон фона {suffix}", "Наклон", 0, 0, 0, sign * floor, 1, 1))
    if record.get("corrected") is not None:
        for sign in (-1, 1):
            cases.append(
                (
                    f"Вычитание NOX {'+' if sign > 0 else '−'}{baseline_percent:g}%",
                    "Линия NOX",
                    0,
                    0,
                    0,
                    0,
                    1 + sign * fraction,
                    1,
                )
            )
    fids = {i["spin_fit_id"] for i in items if i.get("spin_fit_id")}
    states = record.get("spin_analyses", {})
    lambda_fids = {
        fid
        for fid in fids
        if states.get(fid, {}).get("recipe", {}).get("baseline_default", {}).get("method")
        in LAMBDA_METHODS
    }
    if lambda_fids:
        for sign in (-1, 1):
            cases.append(
                (
                    f"Локальная λ {'+' if sign > 0 else '−'}{baseline_percent:g}%",
                    "Линия Spintegrate",
                    0,
                    0,
                    0,
                    0,
                    1,
                    1 + sign * fraction,
                )
            )
    low = min(float(i["left"]) - offset for i in items)
    high = max(float(i["right"]) - offset for i in items)
    if not np.isfinite(low + high) or low >= high:
        raise ValueError("Некорректные границы интегралов.")
    ramp = 2 * (x - (low + high) / 2) / (high - low)
    nominal, rows = {}, []
    total = len(cases) * len(items)
    for case_index, (case, factor, dl, dr, level, slope, strength, lam) in enumerate(cases, 1):
        if stop is not None and stop.is_set():
            return rows if keep_partial else []
        trial = copy.deepcopy(record)
        if strength != 1:
            phased = phase_correct(
                record["real"],
                record["imaginary"],
                record["phase0"],
                record["phase1"],
                record["phase_pivot"],
            )
            trial["corrected"] = phased - strength * (phased - signal)
        if level or slope:
            trial["corrected"] = current_signal(trial).copy() - level - slope * ramp
        refits, fit_errors, fit_notes = {}, {}, {}
        for item in items:
            if stop is not None and stop.is_set():
                return rows if keep_partial else []
            number = item["number"]
            label = f"Сценарий {case_index}/{len(cases)} · I{number} · {case}"
            if progress:
                progress(len(rows), total, label)
            changed = copy.deepcopy(item)
            changed["left"] += dl
            changed["right"] += dr
            value, status, message = None, "ready", ""
            try:
                fid = item.get("spin_fit_id")
                if factor == "Линия Spintegrate" and fid not in lambda_fids:
                    status, message = "skipped", "У этого интеграла нет локальной λ."
                else:
                    a, b = changed["left"] - offset, changed["right"] - offset
                    if (
                        a >= b
                        or a < x.min()
                        or b > x.max()
                        or np.count_nonzero((x >= a) & (x <= b)) < 3
                    ):
                        raise ValueError(
                            "Изменённая область вне спектра или содержит меньше трёх точек."
                        )
                    if fid:
                        old = states.get(fid)
                        if not old or not fit_current({**record, "spin": old}):
                            raise ValueError(
                                "Подгонка отсутствует или устарела; сначала пересчитайте её."
                            )
                        needs_refit = (
                            dl
                            or dr
                            or level
                            or slope
                            or strength != 1
                            or (lam != 1 and fid in lambda_fids)
                        )
                        if needs_refit and fid not in refits and fid not in fit_errors:
                            try:
                                model = old["models"][0]
                                shift = float(record.get("ppm_shift", 0)) - float(
                                    old["expected"].get("ppm_shift", 0)
                                )
                                recipe = copy.deepcopy(old["recipe"])
                                # Freeze the effective method: crossing a NOX coverage
                                # edge must not silently enable a second subtraction.
                                recipe["baseline_policy"] = "manual"
                                if fid in lambda_fids:
                                    recipe["baseline_default"]["lam"] *= lam
                                for anchor in recipe["fit"].get("component_shapes", []):
                                    anchor["center"] += shift
                                state = run_selected_region(
                                    trial,
                                    recipe,
                                    model["low"] + shift + dl,
                                    model["high"] + shift + dr,
                                )
                                remap_groups(old, state)
                                count = len(state["models"][0]["centers"])
                                if count != len(model["centers"]):
                                    fit_notes[fid] = (
                                        f"Число линий изменилось: {len(model['centers'])} → {count}."
                                    )
                                if not count and len(model["centers"]):
                                    raise ValueError("Все линии модели потеряны.")
                                refits[fid] = state
                                trial["spin_analyses"][fid] = state
                            except (
                                ValueError,
                                RuntimeError,
                                ArithmeticError,
                                KeyError,
                                IndexError,
                            ) as error:
                                fit_errors[fid] = str(error)
                        if fid in fit_errors:
                            raise ValueError(fit_errors[fid])
                    value = float(area_for_context(trial, changed, offset, absolute))
                    if not math.isfinite(value):
                        raise ValueError("Площадь недоступна; возможно, потерян состав группы.")
                    message = fit_notes.get(fid, "")
            except (
                ValueError,
                RuntimeError,
                ArithmeticError,
                KeyError,
                IndexError,
                TypeError,
            ) as error:
                value, status, message = None, "error", str(error)
            if case == "Исходный":
                nominal[number] = value
            base = nominal[number]
            change = value - base if _finite(value) and _finite(base) else None
            relative = 100 * change / abs(base) if base and change is not None else None
            if relative is not None and not math.isfinite(relative):
                relative = None
            rows.append(
                dict(
                    number=number,
                    name=item.get("name", f"I{number}"),
                    case=case,
                    factor=factor,
                    value=value,
                    nominal=base,
                    change=change,
                    percent=relative,
                    status=status,
                    message=message,
                    delta=d,
                    noise=noise,
                    method="Spintegrate" if item.get("spin_fit_id") else "Численный",
                    planned_cases=len(cases),
                )
            )
            if progress:
                progress(len(rows), total, label)
    return rows


def summarize_integral_stability(rows):
    summaries = []
    for number in dict.fromkeys(r["number"] for r in rows):
        group = [r for r in rows if r["number"] == number]
        base = next(r for r in group if r["case"] == "Исходный")
        valid = [r for r in group if r["status"] == "ready" and _finite(r["value"])]
        changed = [r for r in valid if r["case"] != "Исходный" and _finite(r["change"])]
        worst = max(changed, key=lambda r: abs(r["change"]), default=None)
        planned = max(r.get("planned_cases", len(group)) for r in group)
        completed = len({r["case"] for r in group if r.get("completed_case", True)})
        by_factor = {}
        for row in changed:
            if row["percent"] is not None:
                by_factor[row["factor"]] = max(by_factor.get(row["factor"], 0), abs(row["percent"]))
        summaries.append(
            dict(
                number=number,
                name=base["name"],
                method=base["method"],
                nominal=base["value"],
                low=min((r["value"] for r in valid), default=None),
                high=max((r["value"] for r in valid), default=None),
                percent=abs(worst["percent"]) if worst and worst["percent"] is not None else None,
                change=abs(worst["change"]) if worst else None,
                worst=worst["case"] if worst else "—",
                factors=by_factor,
                failed=sum(r["status"] == "error" for r in group),
                completed=completed,
                planned=planned,
                partial=completed < planned,
                notes=list(
                    dict.fromkeys(
                        r["message"] for r in group if r["message"] and r["status"] != "skipped"
                    )
                ),
            )
        )
    return summaries
