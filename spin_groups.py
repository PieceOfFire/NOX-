"""Explicit, disjoint component membership; intervals are fit domains, not groups."""

import copy
import numpy as np
from scipy.optimize import linear_sum_assignment
from spin_backend import fit_current, model_component_areas


def recipe_for_groups(recipe, state=None, shift=0.):
    """Build stable per-spectrum shape anchors; never share raw indices between fits."""
    result = copy.deepcopy(recipe)
    result["fit"]["component_shapes"] = []
    if not state:
        return result
    model = state["models"][0]
    seeds = candidate_centers(state)
    tolerances = local_tolerances(seeds, matching_tolerance(model))
    default = result["fit"]["line_shape"]
    result["group_default_shape"] = default
    automatic = next((g for g in state.get("groups", []) if g.get("automatic")), None)
    if automatic and automatic.get("line_shape"):
        result["fit"]["line_shape"] = automatic["line_shape"]
    for group in state.get("groups", []):
        shape = group.get("line_shape", default)
        if shape == result["fit"]["line_shape"]:
            continue
        for index in group["components"]:
            result["fit"]["component_shapes"].append(dict(
                center=float(seeds[index]) + shift, shape=shape,
                tolerance=float(tolerances[index]),
            ))
    return result


def component_matches(old, new, tolerance):
    """One-to-one matching; vanished lines must never silently become neighbours."""
    old, new = np.asarray(old, dtype=float), np.asarray(new, dtype=float)
    if not len(old) or not len(new):
        return {}
    distance = np.abs(old[:, None] - new[None, :])
    tolerances = local_tolerances(old, tolerance)
    rows, cols = linear_sum_assignment(np.where(distance <= tolerances[:, None], distance, 1e6 + distance))
    return {int(i): int(j) for i, j in zip(rows, cols) if distance[i, j] <= tolerances[i]}


def local_tolerances(centers, tolerance):
    centers = np.asarray(centers, dtype=float)
    values = np.broadcast_to(tolerance, centers.shape).copy()
    if len(centers) > 1:
        distances = np.abs(centers[:, None] - centers)
        np.fill_diagonal(distances, np.inf)
        values = np.minimum(values, .45 * np.min(distances, axis=1))
    return values


def candidate_centers(state):
    """Identity is the detected extremum, not its shape-dependent fitted center.

    Old models reconstruct it from saved center shifts. Inconsistent or absent
    metadata falls back to conservative fitted-center matching.
    """
    model = state["models"][0]
    centers = np.asarray(model["centers"])
    shifts = np.asarray(model.get("diagnostics", {}).get("center_shifts_hz", []))
    sf = state.get("sf_mhz", 0)
    if shifts.shape != centers.shape or not np.isfinite(shifts).all() or not sf:
        return centers
    seeds = centers - shifts / sf
    saved = model.get("picked_centers")
    if saved is not None and (np.shape(saved) != centers.shape or not np.allclose(saved, seeds, atol=1e-9, rtol=0)):
        return centers
    return seeds


def matching_tolerance(model):
    step = abs(float(np.median(np.diff(model["x"]))))
    return max(2 * step, 0.005)


def remap_groups(old_state, new_state):
    result = copy.deepcopy(old_state.get("groups", []))
    old = old_state["models"][0]
    new = new_state["models"][0]
    # Existing assignments remain local to this spectrum. New lines are unassigned.
    shift = float(new_state.get("expected", {}).get("ppm_shift") or 0) - float(
        old_state.get("expected", {}).get("ppm_shift") or 0)
    matches = component_matches(candidate_centers(old_state) + shift, candidate_centers(new_state),
                                matching_tolerance(old))
    for group in result:
        if group.get("automatic", False):
            continue
        indices = group.get("components", [])
        group["missing"] = int(group.get("missing", 0)) + sum(i not in matches for i in indices)
        group["components"] = [matches[i] for i in indices if i in matches]
    # The untouched whole-region output is dynamic, not a manually assigned
    # chemical group. New/disappeared candidates must not leave it incomplete.
    refresh_automatic_groups(result, len(new["centers"]))
    new_state["groups"] = result


def refresh_automatic_groups(groups, count):
    """Update the dynamic aggregate without taking members from manual groups."""
    reserved = {i for g in groups if not g.get("automatic", False) for i in g["components"]}
    for group in groups:
        if group.get("automatic", False):
            group.update(components=[i for i in range(count) if i not in reserved], missing=0)


def validate_groups(state):
    groups = state.get("groups", [])
    used, ids = set(), set()
    count = len(state["models"][0]["centers"])
    if sum(g.get("automatic", False) is True for g in groups) > 1:
        raise ValueError("У модели может быть только одна автоматическая группа.")
    for group in groups:
        gid = group.get("id")
        if not isinstance(gid, str) or not gid or gid in ids:
            raise ValueError("Некорректный идентификатор группы компонент.")
        ids.add(gid)
        if type(group.get("automatic", False)) is not bool:
            raise ValueError("Некорректный признак автоматического состава группы.")
        if not isinstance(group.get("name"), str) or not group["name"].strip():
            raise ValueError("У группы должно быть название.")
        if type(group.get("missing", 0)) is not int or group.get("missing", 0) < 0:
            raise ValueError("Некорректное число потерянных компонент.")
        if "line_shape" in group:
            from spintegrate.profiles import LINE_SHAPES
            if group["line_shape"] not in LINE_SHAPES:
                raise ValueError("Некорректная форма итогового интеграла.")
        for index in group.get("components", []):
            if type(index) is not int or not 0 <= index < count or index in used:
                raise ValueError("Компоненты групп пересекаются или отсутствуют в модели.")
            used.add(index)


def group_area(record, state, group_id, *, absolute=False):
    if not fit_current({**record, "spin": state}):
        return float("nan")
    group = next((g for g in state.get("groups", []) if g["id"] == group_id), None)
    if group is None or group.get("missing", 0):
        return float("nan")
    model = state["models"][0]
    indices = group["components"]
    if any(type(i) is not int or not 0 <= i < len(model["centers"]) for i in indices):
        return float("nan")
    terms = model_component_areas(model)[indices]
    return float(np.sum(np.abs(terms) if absolute else terms))
