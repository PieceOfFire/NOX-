"""Read-only, JSON-safe review checks for manual series processing."""

from __future__ import annotations

import math

import numpy as np

from formula_document import FormulaDocument
from formula_engine import FormulaEngine
from sensitivity import area_for_context
from signal_quality import fit_quality, integral_quality
from spin_backend import current_signal, fit_current
from spin_groups import validate_groups


def _unique(messages):
    return list(dict.fromkeys(str(message) for message in messages))


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def inspect_batch_record(record, formula_payload=None):
    """Re-evaluate actual edited data, not remembered worker warnings.

    Formula evaluation always uses physical areas, independently of the selected
    display units. Missing/stale values are ``None``, never a convenient zero.
    Background/residual quality heuristics are advisory, not acceptance gates.
    """
    warnings, blockers, incomplete = [], [], []
    result = dict(
        warnings=warnings,
        blocking_issues=blockers,
        incomplete_fits=incomplete,
        integrals={},
        outputs=[],
    )
    result["phase_signature"] = {
        name: _number(record.get(name, default))
        for name, default in (("phase0", 0), ("phase1", 0), ("phase_pivot", 0.5), ("ppm_shift", 0))
    }
    if any(value is None for value in result["phase_signature"].values()):
        blockers.append("Фаза и смещение спектра должны быть конечными числами.")
    try:
        if any(value is None for value in result["phase_signature"].values()):
            raise ValueError("фаза или смещение не являются конечными числами")
        ppm = np.asarray(record["ppm"], dtype=float)
        signal = np.asarray(current_signal(record), dtype=float)
        if (
            ppm.ndim != 1
            or len(ppm) < 3
            or ppm.shape != signal.shape
            or not np.isfinite(ppm).all()
            or not np.isfinite(signal).all()
            or not (np.all(np.diff(ppm) > 0) or np.all(np.diff(ppm) < 0))
        ):
            raise ValueError("Шкала ppm и сигнал должны быть согласованы и конечны.")
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        blockers.append(f"Некорректные данные спектра: {error}")
        ppm, signal = None, None

    items = record.get("integrals", [])
    if not isinstance(items, list):
        blockers.append("Некорректный список интегралов.")
        items = []
    if not items:
        blockers.append(
            "Нет интегралов. Примените шаблон серии или создайте области перед подтверждением."
        )
    absolute = record.get("integration_method") == "Сумма модулей относительно 0"
    checked_fits, fit_errors = set(), {}

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            blockers.append(f"Некорректный интеграл в строке {index + 1}.")
            continue
        number = item.get("number", index + 1)
        label = f"I{number}"
        key = str(number)
        if type(number) is not int or number < 1:
            blockers.append(f"{label}: некорректный номер интеграла.")
            continue
        if key in result["integrals"]:
            blockers.append(f"{label}: номер интеграла повторяется.")
            result["integrals"][key] = None
            continue
        result["integrals"][key] = None
        try:
            low, high = float(item["left"]), float(item["right"])
            if not (math.isfinite(low) and math.isfinite(high) and low < high):
                raise ValueError("неверные границы области")
            if ppm is None:
                raise ValueError("сигнал недоступен")
            if low < ppm.min() or high > ppm.max():
                raise ValueError("область выходит за пределы спектра")
            count = int(np.count_nonzero((ppm >= low) & (ppm <= high)))
            if count < 3:
                raise ValueError("в области меньше трёх точек спектра")
            fid = item.get("spin_fit_id")
            if fid:
                state = record.get("spin_analyses", {}).get(fid)
                if fid not in checked_fits:
                    checked_fits.add(fid)
                    try:
                        if not state or not fit_current({**record, "spin": state}):
                            raise ValueError("подгонка отсутствует или устарела")
                        if not state.get("models"):
                            raise ValueError("в подгонке нет модели")
                        model = state["models"][0]
                        x = np.asarray(model["x"], dtype=float)
                        observed = np.asarray(model["corrected"], dtype=float)
                        fitted = np.asarray(model["fitted"], dtype=float)
                        if (
                            x.ndim != 1
                            or len(x) < 3
                            or x.shape != observed.shape
                            or x.shape != fitted.shape
                            or not all(np.isfinite(a).all() for a in (x, observed, fitted))
                        ):
                            raise ValueError("неверные данные модели")
                        if not np.isfinite(np.asarray(model["centers"], dtype=float)).all():
                            raise ValueError("неверные центры линий модели")
                        validate_groups(state)
                        quality = fit_quality(model)
                        warnings.extend(
                            f"Spintegrate {fid}: {message}" for message in quality["warnings"]
                        )
                    except (KeyError, IndexError, TypeError, ValueError, ArithmeticError) as error:
                        fit_errors[fid] = str(error)
                if fid in fit_errors:
                    incomplete.append(str(fid))
                    raise ValueError(f"Spintegrate: {fit_errors[fid]}")
                gid = item.get("spin_group_id")
                if gid:
                    group = next((g for g in state.get("groups", []) if g.get("id") == gid), None)
                    if group is None:
                        incomplete.append(str(fid))
                        raise ValueError("в модели отсутствует группа интеграла")
                    if group.get("missing", 0):
                        incomplete.append(str(fid))
                        raise ValueError(f"в группе потеряно линий: {group['missing']}")
                    # An explicitly assigned empty manual group means confirmed
                    # absence, unlike an automatic empty aggregate.
                    if not group.get("components") and group.get("automatic"):
                        incomplete.append(str(fid))
                        raise ValueError(
                            "автоматическая группа пуста; подтвердите отсутствие сигнала вручную"
                        )
                elif not len(state["models"][0]["centers"]):
                    incomplete.append(str(fid))
                    raise ValueError("в модели не найдены линии")
            value = _number(area_for_context(record, item, 0.0, absolute))
            if value is None:
                if fid:
                    incomplete.append(str(fid))
                raise ValueError("площадь недоступна или не является конечным числом")
            result["integrals"][key] = value
            if not fid and count >= 8:
                quality = integral_quality(ppm, signal, low, high, absolute=absolute)
                warnings.extend(f"{label}: {message}" for message in quality["warnings"])
        except (KeyError, IndexError, TypeError, ValueError, ArithmeticError) as error:
            blockers.append(f"{label}: {error}.")

    if formula_payload:
        try:
            document = FormulaDocument.from_preset(formula_payload, items)
            engine = FormulaEngine(
                document.blocks,
                lambda block: result["integrals"].get(str(block.get("area_number"))),
            )
            outputs = engine.outputs()
            if document.blocks and not outputs:
                blockers.append("В выбранной формуле нет блоков вывода.")
            for bid, name, output in outputs:
                value = _number(output.value)
                ready = output.status == "ready" and value is not None
                message = (
                    str(output.message)
                    .replace("отсутствует: используется 0", "отсутствует: расчёт недоступен")
                    .replace("результат принят за 0", "расчёт недоступен")
                )
                result["outputs"].append(
                    dict(
                        id=int(bid),
                        name=name,
                        value=value if ready else None,
                        status="ready" if ready else "error",
                        message=message,
                    )
                )
                if not ready:
                    blockers.append(f"Формула «{name}»: {message}.")
        except (KeyError, IndexError, TypeError, ValueError, ArithmeticError) as error:
            blockers.append(f"Формула не может быть рассчитана: {error}.")
    result["warnings"] = _unique(warnings)
    result["blocking_issues"] = _unique(blockers)
    result["incomplete_fits"] = _unique(incomplete)
    return result
