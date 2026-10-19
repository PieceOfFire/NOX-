"""Deterministic perturbations, not a statistical confidence interval."""

from __future__ import annotations

import copy
import numpy as np

from formula_engine import FormulaEngine
from integration import integrate_region
from base_line import phase_correct
from signal_quality import noise_sigma
from spin_backend import current_signal, fit_current, run_selected_region, selected_region_area
from spin_groups import group_area, remap_groups


def area_for_context(record, item, offset, absolute):
    fid = item.get("spin_fit_id")
    if fid:
        state = record.get("spin_analyses", {}).get(fid)
        if state is None:
            return float("nan")
        if item.get("spin_group_id"):
            return group_area(record, state, item["spin_group_id"], absolute=absolute)
        return selected_region_area(
            record, state, item["left"], item["right"], absolute=absolute, x_offset=offset
        )
    x = np.asarray(record["ppm"])
    if item["left"] - offset < np.min(x) or item["right"] - offset > np.max(x):
        raise ValueError("Область интеграла выходит за спектр")
    return integrate_region(
        record["ppm"],
        current_signal(record),
        item["left"],
        item["right"],
        absolute=absolute,
        x_offset=offset,
    )


def evaluate_contexts(blocks, record, contexts, absolute):
    def provider(block):
        bid = next((bid for bid, b in blocks.items() if b is block), None)
        context = contexts.get(bid)
        if context is None or context[0] is None:
            return None
        item, offset = context
        return area_for_context(record, item, offset, absolute)

    return FormulaEngine(blocks, provider).outputs()


def sensitivity_case_count(record, contexts):
    has_fits = any(i and i.get("spin_fit_id") for i, _ in contexts.values())
    return 7 if record.get("corrected") is not None or has_fits else 5


def analyze_sensitivity(
    record,
    blocks,
    contexts,
    *,
    delta=0.001,
    baseline_percent=5.0,
    absolute=False,
    stop=None,
    progress=None,
    keep_partial=False,
):
    if not np.isfinite(delta) or delta <= 0 or not 0 < baseline_percent <= 50:
        raise ValueError("δ должно быть > 0, изменение линии — от 0 до 50%.")
    baseline = evaluate_contexts(blocks, record, contexts, absolute)
    if not baseline:
        raise ValueError("В формуле нет блоков вывода.")
    noise = noise_sigma(current_signal(record))
    step = abs(float(np.median(np.diff(record["ppm"]))))
    delta = max(delta, step)
    cases = [("Исходный", 0, 0, 1, 1)]
    cases += [("Границы шире", delta, 0, 1, 1), ("Границы уже", -delta, 0, 1, 1)]
    cases += [("Фон +σ", 0, noise, 1, 1), ("Фон −σ", 0, -noise, 1, 1)]
    has_fits = any(i and i.get("spin_fit_id") for i, _ in contexts.values())
    if record.get("corrected") is not None or has_fits:
        fraction = baseline_percent / 100
        cases += [
            (f"Вычитание линии −{baseline_percent:g}% / λ×0.8", 0, 0, 1 - fraction, 0.8),
            (f"Вычитание линии +{baseline_percent:g}% / λ×1.2", 0, 0, 1 + fraction, 1.2),
        ]
    rows = []
    for case_index, (label, expand, floor, strength, lam) in enumerate(cases):
        if stop is not None and stop.is_set():
            return rows if keep_partial else []
        progress_label = f"Сценарий {case_index + 1}/{len(cases)} · {label}"
        if progress:
            progress(case_index, len(cases), progress_label)
        try:
            trial = copy.deepcopy(record)
            moved = copy.deepcopy(contexts)
            if label != "Исходный":
                seen = set()
                for item, _ in moved.values():
                    if item is not None and id(item) not in seen:
                        seen.add(id(item))
                        item["left"] -= expand
                        item["right"] += expand
                        if item["left"] >= item["right"]:
                            raise ValueError("Суженная область пуста")
                if strength != 1 and record.get("corrected") is not None:
                    phased = phase_correct(
                        record["real"],
                        record["imaginary"],
                        record["phase0"],
                        record["phase1"],
                        record["phase_pivot"],
                    )
                    trial["corrected"] = phased - strength * (phased - current_signal(record))
                if floor:
                    trial["corrected"] = current_signal(trial).copy() - floor
                for fid in {
                    i.get("spin_fit_id") for i, _ in contexts.values() if i and i.get("spin_fit_id")
                }:
                    old = record.get("spin_analyses", {}).get(fid)
                    if not old or not fit_current({**record, "spin": old}):
                        raise ValueError("Подгонка устарела")
                    model = old["models"][0]
                    shift = float(record.get("ppm_shift", 0)) - float(
                        old["expected"].get("ppm_shift", 0)
                    )
                    recipe = copy.deepcopy(old["recipe"])
                    for anchor in recipe["fit"].get("component_shapes", []):
                        anchor["center"] += shift
                    recipe["baseline_default"]["lam"] *= lam
                    state = run_selected_region(
                        trial, recipe, model["low"] + shift - expand, model["high"] + shift + expand
                    )
                    remap_groups(old, state)
                    trial["spin_analyses"][fid] = state
            results = evaluate_contexts(blocks, trial, moved, absolute)
            for bid, name, result in results:
                rows.append(
                    dict(
                        case=label,
                        output_id=bid,
                        name=name,
                        value=result.value,
                        status=result.status,
                        message=result.message,
                        planned_cases=len(cases),
                    )
                )
        except (ValueError, RuntimeError, ArithmeticError) as error:
            for bid, name, _ in baseline:
                rows.append(
                    dict(
                        case=label,
                        output_id=bid,
                        name=name,
                        value=None,
                        status="error",
                        message=str(error),
                        planned_cases=len(cases),
                    )
                )
        if progress:
            progress(case_index + 1, len(cases), progress_label)
    return rows


def summarize_sensitivity(rows):
    summaries = []
    for bid in dict.fromkeys(r["output_id"] for r in rows):
        subset = [r for r in rows if r["output_id"] == bid]
        values = [
            r["value"]
            for r in subset
            if r["status"] == "ready" and r["value"] is not None and np.isfinite(r["value"])
        ]
        nominal = subset[0]["value"]
        planned = max(r.get("planned_cases", len(subset)) for r in subset)
        completed = len({r["case"] for r in subset if r.get("completed_case", True)})
        spread = (
            max((abs(v - nominal) for v in values), default=None) if nominal is not None else None
        )
        relative = 100 * spread / abs(nominal) if nominal and spread is not None else None
        summaries.append(
            dict(
                name=subset[0]["name"],
                nominal=nominal,
                low=min(values) if values else None,
                high=max(values) if values else None,
                percent=relative,
                failed=sum(r["status"] != "ready" for r in subset),
                completed=completed,
                planned=planned,
                partial=completed < planned,
            )
        )
    return summaries
