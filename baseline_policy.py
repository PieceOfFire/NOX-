"""Coordinate local Spintegrate defaults with the applied NOX correction."""

import copy
import numpy as np


def corrected_region(record, left, right, offset=0):
    """Only disable local subtraction when NOX covers the entire requested region."""
    if record.get("corrected") is None:
        return False
    steps = record.get("baseline_steps", [])
    if not steps or record.get("baseline_source_unknown", False):
        # Older corrected spectra have no reproducible range metadata. Avoid a
        # second automatic subtraction, while still allowing a manual override.
        return True
    low, high = sorted((float(left) - offset, float(right) - offset))
    x = np.asarray(record["ppm"], dtype=float)
    tolerance = abs(float(np.median(np.diff(x)))) * 1.5 if len(x) > 1 else 1e-9
    covered = low
    for step in sorted(steps, key=lambda s: s["low"]):
        # A deliberately selected no-op is not an applied background subtraction.
        if step.get("method") == "Spintegrate: none":
            continue
        a, b = float(step["low"]), float(step["high"])
        if a > covered + tolerance:
            break
        if b >= covered - tolerance:
            covered = max(covered, b)
        if covered >= high - tolerance:
            return True
    return False


def resolve_spin_baseline(recipe, record, left, right, offset=0):
    result = copy.deepcopy(recipe)
    if result.get("baseline_policy", "manual") == "nox":
        fallback = result.get("baseline_fallback_method", result["baseline_default"]["method"])
        result["baseline_fallback_method"] = fallback
        result["baseline_default"]["method"] = (
            "none" if corrected_region(record, left, right, offset) else fallback
        )
    return result
