"""Read-only explanations and comparisons for the live fitting workspace."""

import numpy as np

from spin_backend import model_component_areas
from spin_groups import candidate_centers, component_matches, matching_tolerance


def group_value(state, group, *, absolute=False):
    if group.get("missing", 0):
        return None
    areas = model_component_areas(state["models"][0])
    indices = group.get("components", [])
    if any(not 0 <= i < len(areas) for i in indices):
        return None
    value = float(np.sum(np.abs(areas[indices]) if absolute else areas[indices]))
    return value if np.isfinite(value) else None


def compare_groups(before, after, *, absolute=False):
    """Area changes by stable group ID; new groups require complete line matching.

    Never compare a partial group, silently use a nearby line or divide by zero.
    A changed membership is explicitly labelled, not presented as a shape effect.
    """
    if not after:
        return []
    old_groups = {g["id"]: g for g in before.get("groups", [])} if before else {}
    matches = {}
    if before:
        shift = float(after.get("expected", {}).get("ppm_shift") or 0) - float(
            before.get("expected", {}).get("ppm_shift") or 0
        )
        matches = component_matches(
            candidate_centers(after),
            candidate_centers(before) + shift,
            matching_tolerance(after["models"][0]),
        )
    rows = []
    for group in after.get("groups", []):
        old = old_groups.get(group["id"])
        note = "Тот же интеграл"
        current = group_value(after, group, absolute=absolute)
        previous = None
        if old is not None:
            previous = group_value(before, old, absolute=absolute)
            mapped = {matches[i] for i in group["components"] if i in matches}
            if len(mapped) != len(group["components"]) or mapped != set(old["components"]):
                note = "Изменён состав интеграла"
        elif before:
            indices = group["components"]
            if indices and all(i in matches for i in indices) and not group.get("missing", 0):
                previous = group_value(
                    before,
                    {"components": [matches[i] for i in indices]},
                    absolute=absolute,
                )
                note = "Линии сопоставлены с «До»"
            else:
                note = "Нет полного соответствия в «До»"
        else:
            note = "Сначала зафиксируйте «До»"
        delta = None
        if previous is not None and current is not None and previous != 0:
            delta = 100 * (current - previous) / abs(previous)
        if group.get("missing", 0) or (old and old.get("missing", 0)):
            note = "Неполный состав — сравнение недоступно"
        elif previous == 0:
            note = "Площадь «До» равна нулю; Δ% не определено"
        rows.append(
            dict(
                id=group["id"],
                name=group["name"],
                before=previous,
                after=current,
                delta_pct=delta,
                note=note,
            )
        )
    return rows


def comparison_curve(before, after):
    """Previous fit in the CURRENT local-baseline frame, not a shifted fake fit."""
    old, new = before["models"][0], after["models"][0]
    ox, nx = np.asarray(old["x"]), np.asarray(new["x"])
    shift = float(after.get("expected", {}).get("ppm_shift") or 0) - float(
        before.get("expected", {}).get("ppm_shift") or 0
    )
    order = np.argsort(ox)
    full = np.asarray(old["fitted"]) + np.asarray(old["baseline"])
    return (
        np.interp(nx, ox[order] + shift, full[order], left=np.nan, right=np.nan) - new["baseline"]
    )


def workspace_issues(
    records, states, *, failures=None, parameter_error=None, anchors=None, offsets=None
):
    """Structured targets for warnings; no processing data are modified."""
    issues = []
    if parameter_error:
        issues.append(
            dict(
                kind="parameter",
                sid=None,
                group_id=None,
                centers=[],
                severity="Ошибка",
                message=str(parameter_error),
                action="Открыть параметры; исправить значение или нажать «Назад».",
            )
        )
    for sid, message in (failures or {}).items():
        issues.append(
            dict(
                kind="fit",
                sid=sid,
                group_id=None,
                centers=[],
                severity="Ошибка",
                message=str(message),
                action="Открыть параметры и повторить подгонку этого спектра.",
            )
        )
    for sid, state in states.items():
        model = state["models"][0]
        centers = candidate_centers(state)
        for group in state.get("groups", []):
            if not group.get("missing", 0):
                continue
            expected = list((anchors or {}).get((sid, group["id"]), []))
            if not expected:
                # A series may retain the group's complete composition elsewhere.
                for other_sid, other in states.items():
                    good = next(
                        (
                            g
                            for g in other.get("groups", [])
                            if g["id"] == group["id"] and not g.get("missing", 0)
                        ),
                        None,
                    )
                    if good:

                        def offset(key):
                            return float((offsets or {}).get(key, (offsets or {}).get(str(key), 0)))

                        expected = (
                            candidate_centers(other)[good["components"]]
                            + offset(other_sid)
                            - offset(sid)
                        ).tolist()
                        break
            matches = component_matches(expected, centers, matching_tolerance(model))
            lost = [float(c) for i, c in enumerate(expected) if i not in matches]
            issues.append(
                dict(
                    kind="missing",
                    sid=sid,
                    group_id=group["id"],
                    centers=lost,
                    severity="Ошибка",
                    message=f"«{group['name']}»: не найдены линии ({group['missing']}).",
                    action="Проверьте порог/форму; выберите нужные линии и замените состав. "
                    "Нулевой интеграл подтверждайте только при реальном отсутствии сигнала."
                    + (
                        " Координаты прежних линий неизвестны; показана вся область."
                        if not lost
                        else ""
                    ),
                )
            )
        used = {i for g in state.get("groups", []) for i in g["components"]}
        unused = [i for i in range(len(centers)) if i not in used]
        if unused:
            issues.append(
                dict(
                    kind="unassigned",
                    sid=sid,
                    group_id=None,
                    centers=centers[unused].tolist(),
                    indices=unused,
                    severity="Проверить",
                    message=f"Линий вне итоговых интегралов: {len(unused)}.",
                    action="Назначьте их интегралу или оставьте исключёнными, если они не нужны.",
                )
            )
        if not len(centers):
            issues.append(
                dict(
                    kind="empty",
                    sid=sid,
                    group_id=None,
                    centers=[],
                    severity="Проверить",
                    message="Не найдено ни одной линии.",
                    action="Запустите автонастройку или проверьте порог в экспертном режиме.",
                )
            )
    return issues
