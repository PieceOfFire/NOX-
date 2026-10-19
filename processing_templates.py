"""Portable processing recipes: raw arrays stay in the spectrum, never in a recipe."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np

from alignment import find_x_shift
from base_line import (
    arpls_baseline,
    auto_phase_parameters,
    phase_correct,
    spline_baseline,
    whittaker_baseline,
    whittaker_lambda,
)
from formula_document import FormulaDocument
from baseline_methods import core_method, validate_settings, extended_baseline
from spin_backend import current_signal, fit_current, run_selected_region, recipe_session
from spin_groups import (
    component_matches,
    matching_tolerance,
    validate_groups,
    refresh_automatic_groups,
    candidate_centers,
)

FORMAT = "himera-processing-template"
BASELINES = ("Адаптивный arPLS", "Сглаживающий spline", "Фильтр Уиттакера")


def validate_template(template):
    if (
        not isinstance(template, dict)
        or template.get("format") != FORMAT
        or template.get("version") != 1
    ):
        raise ValueError("Это не шаблон обработки Himera.")
    # JSON rejects non-finite values and eliminates arbitrary Python objects.
    json.dumps(template, allow_nan=False)
    for key in ("phase0", "phase1", "pivot"):
        if not np.isfinite(float(template["phase"][key])):
            raise ValueError("Некорректная фаза шаблона.")
    for step in template["baseline_steps"]:
        extended = core_method(step["method"])
        if (step["method"] not in BASELINES and extended is None) or step["low"] >= step["high"]:
            raise ValueError("Некорректные параметры базовой линии.")
        if extended is not None:
            validate_settings(extended, step.get("parameters", {}))
        if step["polarity"] not in ("auto", -1, 0, 1):
            raise ValueError("Некорректная полярность пиков.")
        if step["method"] == BASELINES[2] and (step["filter_hz"] <= 0 or step["smoothness"] <= 0):
            raise ValueError("Параметры Уиттакера должны быть положительными.")
    numbers = set()
    ids = set()
    for item in template["integrals"]:
        number = item["number"]
        if (
            type(number) is not int
            or number < 1
            or number in numbers
            or item["left"] >= item["right"]
        ):
            raise ValueError("Номера интегралов повторяются или неверно заданы границы.")
        numbers.add(number)
        iid = item["id"]
        if type(iid) is not int or iid < 1 or iid in ids:
            raise ValueError("Некорректный идентификатор интеграла.")
        ids.add(iid)
        if item.get("spin_fit_id") and item["spin_fit_id"] not in template["fits"]:
            raise ValueError("В шаблоне отсутствует подгонка интеграла.")
    for fit in template["fits"].values():
        recipe_session(fit["recipe"])
        if fit["low"] >= fit["high"]:
            raise ValueError("Некорректная область подгонки.")
        centers = np.asarray(fit["centers"], dtype=float)
        if "picked_centers" in fit and (
            np.shape(fit["picked_centers"]) != centers.shape
            or not np.isfinite(fit["picked_centers"]).all()
        ):
            raise ValueError("Некорректные исходные центры шаблона.")
        validate_groups({"models": [{"centers": centers}], "groups": fit["groups"]})
        used = set()
        for group in fit["groups"]:
            indices = group["components"]
            if any(type(i) is not int or not 0 <= i < len(centers) or i in used for i in indices):
                raise ValueError("Некорректный состав групп шаблона.")
            used.update(indices)
    for item in template["integrals"]:
        if item.get("spin_group_id"):
            fit = template["fits"].get(item.get("spin_fit_id"), {})
            if not any(g["id"] == item["spin_group_id"] for g in fit.get("groups", [])):
                raise ValueError("В шаблоне отсутствует группа интеграла.")
    FormulaDocument.from_preset(template["formula"], template["integrals"])
    ref = template["reference"]
    x, y = np.asarray(ref["ppm"], dtype=float), np.asarray(ref["signal"], dtype=float)
    if (
        x.ndim != 1
        or len(x) < 20
        or x.shape != y.shape
        or not (np.all(np.diff(x) > 0) or np.all(np.diff(x) < 0))
    ):
        raise ValueError("Некорректный спектр-образец в шаблоне.")
    if len(ref["limits"]) != 2 or ref["limits"][0] >= ref["limits"][1]:
        raise ValueError("Некорректная область выравнивания.")
    if template["integration_method"] not in ("Обычная площадь", "Сумма модулей относительно 0"):
        raise ValueError("Неизвестный метод интегрирования.")
    return template


def make_template(record, integrals, formula, *, offset=0.0, limits=None):
    steps = copy.deepcopy(record.get("baseline_steps", []))
    if record.get("corrected") is not None and (not steps or record.get("baseline_source_unknown")):
        raise ValueError(
            "Параметры старой базовой линии не сохранены. Примените фазу и коррекцию заново перед созданием шаблона."
        )
    for step in steps:
        step["low"] += offset
        step["high"] += offset
    fits = {}
    for item in integrals:
        fid = item.get("spin_fit_id")
        if not fid or fid in fits:
            continue
        state = record.get("spin_analyses", {}).get(fid)
        if not state or not fit_current({**record, "spin": state}):
            raise ValueError("Подгонка устарела. Пересчитайте её перед сохранением шаблона.")
        if any(g.get("missing", 0) for g in state.get("groups", [])):
            raise ValueError("В группе потеряны линии. Проверьте её состав.")
        model = state["models"][0]
        delta = float(record.get("ppm_shift", 0)) - float(state["expected"].get("ppm_shift", 0))
        fits[fid] = dict(
            recipe=copy.deepcopy(state["recipe"]),
            low=float(model["low"]) + delta + offset,
            high=float(model["high"]) + delta + offset,
            centers=(np.asarray(model["centers"]) + delta + offset).tolist(),
            picked_centers=(candidate_centers(state) + delta + offset).tolist(),
            groups=copy.deepcopy(state.get("groups", [])),
        )
        for anchor in fits[fid]["recipe"]["fit"].get("component_shapes", []):
            anchor["center"] += delta + offset
    x = np.asarray(record["ppm"]) + offset
    y = phase_correct(
        record["real"],
        record["imaginary"],
        record["phase0"],
        record["phase1"],
        record["phase_pivot"],
    )
    # A compact reference for alignment; no corrected spectrum or fitted curves are copied.
    stride = max(1, int(np.ceil(len(x) / 16000)))
    result = dict(
        format=FORMAT,
        version=1,
        name=record["name"],
        phase=dict(
            phase0=float(record["phase0"]),
            phase1=float(record["phase1"]),
            pivot=float(record["phase_pivot"]),
        ),
        baseline_steps=steps,
        integrals=copy.deepcopy(integrals),
        fits=fits,
        integration_method=record.get("integration_method", "Обычная площадь"),
        integration_display=record.get("integration_display", "Площадь"),
        reference=dict(
            ppm=x[::stride].tolist(),
            signal=y[::stride].tolist(),
            limits=list(limits or (float(x.min()), float(x.max()))),
        ),
        formula=copy.deepcopy(formula),
    )
    return validate_template(result)


def load_template(path):
    try:
        return validate_template(json.loads(Path(path).read_text(encoding="utf-8")))
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError("Неполный или повреждённый шаблон обработки.") from error


def apply_baseline_steps(record, steps, *, strength=1.0):
    """Reproduce ordered subtractions from the phased input, preserving physical scale."""
    signal = phase_correct(
        record["real"],
        record["imaginary"],
        record["phase0"],
        record["phase1"],
        record["phase_pivot"],
    )
    ppm = np.asarray(record["ppm"])
    for step in steps:
        tolerance = abs(float(np.median(np.diff(ppm)))) * 1.5
        if step["low"] < np.min(ppm) - tolerance or step["high"] > np.max(ppm) + tolerance:
            raise ValueError("Область коррекции выходит за спектр.")
        mask = (ppm >= step["low"] - 1e-9) & (ppm <= step["high"] + 1e-9)
        if np.count_nonzero(mask) < 8:
            raise ValueError("Область коррекции не покрывается спектром.")
        values = signal[mask]
        extended = core_method(step["method"])
        if extended is not None:
            base, _ = extended_baseline(
                ppm[mask], values, extended, step.get("parameters", {}), polarity=step["polarity"]
            )
        elif step["method"] == BASELINES[0]:
            base = arpls_baseline(values, total_points=len(ppm), polarity=step["polarity"])
        elif step["method"] == BASELINES[1]:
            base = spline_baseline(values, polarity=step["polarity"])
        else:
            lam = whittaker_lambda(
                step["filter_hz"], step["smoothness"], record["spectral_width_hz"]
            )
            base = whittaker_baseline(values, lam, polarity=step["polarity"])
        signal[mask] -= base * strength
    return signal if steps else None


def _validate_target(record):
    """Reject incompatible inputs before doing expensive or partial processing."""
    ppm = np.asarray(record["ppm"], dtype=float)
    real = np.asarray(record["real"], dtype=float)
    imaginary = np.asarray(record["imaginary"], dtype=float)
    if (
        ppm.ndim != 1 or len(ppm) < 8
        or real.shape != ppm.shape or imaginary.shape != ppm.shape
        or not all(np.isfinite(values).all() for values in (ppm, real, imaginary))
        or not (np.all(np.diff(ppm) > 0) or np.all(np.diff(ppm) < 0))
    ):
        raise ValueError("Некорректные данные спектра: нужны согласованные 1r, 1i и шкала ppm.")
    if not all(np.isfinite(float(record.get(key, default))) for key, default in (
        ("phase0", 0), ("phase1", 0), ("phase_pivot", .5),
    )):
        raise ValueError("Фаза спектра должна быть конечным числом.")
    return ppm


def apply_template(
    record, template, *, align=True, auto_phase=False,
    phase_mode="template", allow_incomplete=False,
):
    """Replay a recipe on independent data, never accumulating a previous baseline.

    ``phase_mode='preserve'`` is the manual-series path: only the operator sets
    phase. The legacy default still copies template phase. Incomplete Spintegrate
    assignments can be returned for editing, but remain missing (NaN), never zero.
    """
    template = validate_template(template)
    if phase_mode not in ("template", "preserve"):
        raise ValueError("Неизвестный режим фазы шаблона.")
    if phase_mode == "preserve" and auto_phase:
        raise ValueError("Ручная фаза несовместима с автоматическим фазированием.")
    _validate_target(record)
    result = copy.deepcopy(record)
    phase = template["phase"]
    result["corrected"] = None
    if phase_mode == "template":
        result.update(
            phase0=phase["phase0"], phase1=phase["phase1"],
            phase_pivot=phase["pivot"], phase_reference=None,
        )
    if auto_phase:
        p0, p1, reliable = auto_phase_parameters(
            result["real"], result["imaginary"], pivot=phase["pivot"], return_reliable=True
        )
        if not reliable:
            raise ValueError("Автофаза неуверенная; обработайте спектр вручную.")
        result.update(phase0=p0, phase1=p1)
    shift, correlation = 0.0, None
    if align:
        ref = template["reference"]
        aligned = find_x_shift(
            np.asarray(ref["ppm"]),
            np.asarray(ref["signal"]),
            result["ppm"],
            current_signal(result),
            *ref["limits"],
        )
        shift, correlation = aligned.shift, aligned.correlation
        result["ppm"] = np.asarray(result["ppm"]) + shift
        result["ppm_shift"] = float(result.get("ppm_shift", 0)) + shift
    warnings, incomplete_fits = [], []
    ppm = np.asarray(result["ppm"])
    for item in template["integrals"]:
        if item["left"] < ppm.min() or item["right"] > ppm.max():
            raise ValueError(f"Интеграл I{item['number']} выходит за пределы спектра.")
        if np.count_nonzero((ppm >= item["left"]) & (ppm <= item["right"])) < 2:
            raise ValueError(f"В интеграле I{item['number']} меньше двух точек спектра.")
    for fid, fit in template["fits"].items():
        if fit["low"] < ppm.min() or fit["high"] > ppm.max():
            raise ValueError(f"Область Spintegrate {fid} выходит за пределы спектра.")
    result["baseline_steps"] = copy.deepcopy(template["baseline_steps"])
    result["baseline_source_unknown"] = False
    result["corrected"] = apply_baseline_steps(result, result["baseline_steps"])
    result["integrals"] = copy.deepcopy(template["integrals"])
    result["spin_analyses"] = {}
    result["last_spin_fit"] = None
    for fid, fit in template["fits"].items():
        state = run_selected_region(result, fit["recipe"], fit["low"], fit["high"])
        model = state["models"][0]
        matches = component_matches(
            fit.get("picked_centers", fit["centers"]),
            candidate_centers(state),
            matching_tolerance(model),
        )
        state["groups"] = copy.deepcopy(fit["groups"])
        for group in state["groups"]:
            if group.get("automatic", False):
                continue
            old = group["components"]
            group["components"] = [matches[i] for i in old if i in matches]
            group["missing"] = sum(i not in matches for i in old)
        refresh_automatic_groups(state["groups"], len(model["centers"]))
        if allow_incomplete and not len(model["centers"]):
            # A vanished dynamic whole-area signal needs an explicit operator
            # acknowledgement, just like a vanished named chemical group.
            for group, original in zip(state["groups"], fit["groups"]):
                if group.get("automatic") and original["components"]:
                    group.update(automatic=False, missing=len(original["components"]))
        validate_groups(state)
        missing_groups = [g for g in state["groups"] if g.get("missing", 0)]
        if missing_groups:
            if not allow_incomplete:
                raise ValueError("Не все линии группы найдены: требуется ручная проверка Spintegrate.")
            incomplete_fits.append(fid)
            warnings.extend(
                f"Spintegrate «{group['name']}»: потеряно линий {group['missing']}; "
                "площадь недоступна до проверки состава."
                for group in missing_groups
            )
        result["spin_analyses"][fid] = state
        result["last_spin_fit"] = fid
    for item in result["integrals"]:
        if item["left"] < np.min(result["ppm"]) or item["right"] > np.max(result["ppm"]):
            raise ValueError(f"Интеграл I{item['number']} выходит за пределы спектра.")
    result["next_integral_id"] = max((i["id"] for i in result["integrals"]), default=0) + 1
    result["reference_integral_id"] = None
    result["integration_method"] = template["integration_method"]
    result["integration_display"] = template["integration_display"]
    return result, dict(
        shift=shift, correlation=correlation, warnings=warnings,
        incomplete_fits=incomplete_fits, phase_mode=phase_mode,
    )
