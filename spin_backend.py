"""Local processing of an explicitly selected region with Spintegrate's core.

No Tk widgets live here. Each approximation belongs to a tagged integral;
the working NOX spectrum is never replaced by the result of run_selected_region.
Raw helpers remain for numerical regressions and the first beta's data format.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict
from pathlib import Path

import numpy as np

from base_line import find_processed_spectrum, phase_correct, read_bruker_parameters
from project_store import decode_signal, encode_signal
from baseline_policy import resolve_spin_baseline

ARRAY_FIELDS = ("ppm", "real", "imaginary", "corrected")
SCALAR_FIELDS = (
    "spectral_width_hz",
    "ppm_shift",
    "phase0",
    "phase1",
    "phase_pivot",
    "phase_reference",
    "title",
)
SPIN_DISPLAY = "Вклад Spintegrate (Σ A×w)"


def engine():
    try:
        from spintegrate import core
    except ImportError as exc:
        raise RuntimeError(
            "Для режима Spintegrate установите зависимости Himera: "
            "python -m pip install -r requirements.txt"
        ) from exc
    return core


def explain_fit_message(message):
    """Translate the engine's candidate count into user-facing model terminology."""
    message = re.sub(
        r"найдено (\d+) пиков \(лимит (\d+)\)",
        r"найдено \1 линий-кандидатов (лимит модели \2); это не число итоговых интегралов",
        str(message),
    )
    return message.replace(
        "Повысьте threshold/разделение или сузьте область.",
        "Повысьте «Порог поиска линий» или «Интервал между линиями», либо сузьте область. Лимит модели находится во вкладке «Дополнительно».",
    ).replace(
        "Пики выше threshold не найдены; интеграл = 0.",
        "Линий выше порога не найдено; площадь модели равна 0.",
    )


def data_snapshot(record):
    return {key: copy.deepcopy(record.get(key)) for key in (*ARRAY_FIELDS, *SCALAR_FIELDS)}


def pack_data(data):
    return {
        **{key: data.get(key) for key in SCALAR_FIELDS},
        "arrays": {key: encode_signal(data.get(key)) for key in ARRAY_FIELDS},
    }


def unpack_data(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("arrays"), dict):
        raise ValueError("В проекте нет сохранённого сигнала Himera.")
    size = int(payload["arrays"]["ppm"]["size"])
    if size < 16:
        raise ValueError("В спектре должно быть не меньше 16 точек.")
    result = {key: payload.get(key) for key in SCALAR_FIELDS}
    result.update({key: decode_signal(payload["arrays"].get(key), size) for key in ARRAY_FIELDS})
    for key in ("ppm", "real", "imaginary"):
        if result[key] is None or not np.isfinite(result[key]).all():
            raise ValueError(f"Некорректный массив {key}.")
    if result["corrected"] is not None and not np.isfinite(result["corrected"]).all():
        raise ValueError("Некорректный скорректированный сигнал.")
    delta = np.diff(result["ppm"])
    if not (np.all(delta > 0) or np.all(delta < 0)):
        raise ValueError("ppm-шкала должна быть монотонной.")
    return result


def same_state(a, b):
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and np.array_equal(a, b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_state(a[key], b[key]) for key in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same_state(x, y) for x, y in zip(a, b))
    return a == b


def find_raw_source(source):
    path = Path(source).resolve()
    if path.is_file() and path.suffix.lower() == ".npz":
        return path
    if path.is_file():
        path = path.parent
    if path.parent.name == "pdata":
        path = path.parent.parent
    for candidate in (path, path.parent):
        if (candidate / "fid").is_file() and (candidate / "acqus").is_file():
            return candidate
    raise FileNotFoundError("Не найден одномерный Bruker FID с acqus (или демо-файл NPZ).")


def current_signal(data):
    if data.get("corrected") is not None:
        return np.asarray(data["corrected"])
    return phase_correct(
        data["real"],
        data["imaginary"],
        data.get("phase0", 0),
        data.get("phase1", 0),
        data.get("phase_pivot", 0.5),
    )


def base_data(record):
    spin = record.get("spin")
    if record.get("work_mode") == "spintegrate" and spin and fit_current(record):
        # Re-fitting an unchanged model starts from its original input, not
        # from data with the same local baseline already subtracted once.
        data = data_snapshot(spin["input_snapshot"])
        delta = float(record.get("ppm_shift", 0)) - float(spin["expected"].get("ppm_shift", 0))
        data["ppm"] = data["ppm"] + delta
        data["ppm_shift"] = float(data.get("ppm_shift", 0)) + delta
        return data
    # Manual phase/baseline edits in the Spintegrate workspace are real input
    # changes: current-mode re-analysis must respect them instead of discarding
    # them by silently reading the NOX backup.
    return data_snapshot(record)


def auto_threshold(signal):
    differences = np.diff(signal)
    noise = float(np.median(np.abs(differences - np.median(differences)))) / 0.9539
    return max(6 * noise, float(np.max(np.abs(signal))) * 0.005, 1e-6)


def processed_from_raw(source, processing, processed_reference=None):
    core = engine()
    parameters = core.ProcessingSettings(**processing)
    raw = core.load_raw(source, remove_digital_filter=parameters.remove_digital_filter)
    if Path(source).suffix.lower() != ".npz":
        # Bruker SFO1 is the acquisition frequency, not necessarily the chemical
        # shift reference SF stored by TopSpin. Use the same procs as NOX so a
        # switch to raw FID does not translate every saved interval by several ppm.
        # This is the SR convention also used by nmrglue.bruker.guess_udic.
        try:
            _, procs = find_processed_spectrum(processed_reference or source)
        except FileNotFoundError:
            pass  # Raw-only experiments retain the upstream O1/SFO1 convention.
        else:
            values = read_bruker_parameters(procs)
            sf = float(values["SF"])
            if not np.isfinite(sf) or sf <= 0:
                raise ValueError("Некорректный SF в Bruker procs.")
            raw.carrier_ppm = (raw.sf_mhz - sf) * 1e6 / sf
            raw.sf_mhz = sf
            raw.notes.append(f"Референс ppm: SF={sf:.9g} МГц из {procs} (как в NOX).")
    spectrum = core.process(raw, parameters)
    # The upstream Spectrum intentionally stores the *unphased* complex FFT.
    # Rebuild its phased quadrature for NOX's later manual phase correction.
    ft = spectrum.complex_spectrum.copy()
    if parameters.phase in ("acme", "peak_minima"):
        ft = core.ng.proc_base.ps(ft, p0=spectrum.phases[0], p1=spectrum.phases[1])
    if parameters.phase == "abs":
        ft = spectrum.intensity.astype(complex)
    data = dict(
        ppm=spectrum.ppm.copy(),
        real=spectrum.intensity.copy(),
        imaginary=ft.imag.copy(),
        corrected=None,
        spectral_width_hz=spectrum.sw_hz,
        ppm_shift=0.0,
        phase0=0.0,
        phase1=0.0,
        phase_pivot=0.5,
        phase_reference=None,
        title=f"Himera · FID: {Path(source).name}",
    )
    return spectrum, data


def default_recipe(record=None, limits=None):
    """Initial parameters only; the user always selects the region explicitly."""
    core = engine()
    return dict(
        source="current",
        auto_threshold=False,
        baseline_policy="nox",
        processing=asdict(core.ProcessingSettings(lb_hz=0)),
        fit=asdict(core.FitSettings(line_shape="pseudo_voigt", refine_centers=True)),
        baseline_default=asdict(core.BaselineSettings()),
        regions=[],
    )


def run_selected_region(record, settings, left, right, x_offset=0.0):
    """Run original local baseline/picking/fit only on an explicit selection."""
    recipe = resolve_spin_baseline(settings, record, left, right, x_offset)
    recipe["source"] = "current"
    low, high = sorted((float(left), float(right)))
    recipe["regions"] = [
        dict(
            name="Выбранная область",
            low=low,
            high=high,
            baseline=copy.deepcopy(recipe["baseline_default"]),
        )
    ]
    _, state = run_analysis({**record, "work_mode": "nox"}, recipe, x_offset)
    # The fit belongs to a new integral; it never replaces the working signal.
    state["expected"] = data_snapshot(record)
    state["input_snapshot"] = data_snapshot(record)
    return state


def selected_region_area(record, state, left, right, *, absolute=False, x_offset=0.0):
    return model_area({**record, "spin": state}, left, right, absolute=absolute, x_offset=x_offset)


def recipe_session(recipe, x_offset=0.0):
    core = engine()
    if recipe.get("source") not in ("current", "fid"):
        raise ValueError("Источник: current или fid.")
    regions = [
        core.FitRegion(
            str(r["name"]),
            float(r["low"]) - x_offset,
            float(r["high"]) - x_offset,
            core.BaselineSettings(**r["baseline"]),
        )
        for r in recipe["regions"]
    ]
    if not regions:
        raise ValueError("Добавьте хотя бы одну область обработки.")
    ordered = sorted(regions, key=lambda r: r.low)
    if any(a.high > b.low for a, b in zip(ordered, ordered[1:])):
        raise ValueError(
            "В Himera области подгонки не должны перекрываться. "
            "Близкие пики включите в одну общую область."
        )
    session = core.Session(
        processing=core.ProcessingSettings(**recipe["processing"]),
        fit=core.FitSettings(**recipe["fit"]),
        baseline_default=core.BaselineSettings(**recipe["baseline_default"]),
        regions=regions,
        integrals=[core.IntegrationRegion(r.name, r.name, r.low, r.high) for r in regions],
    )
    session.validate(require_regions=True)
    return session


def run_analysis(record, recipe, x_offset=0.0):
    """Run in a worker, returning independent results (never mutating the record)."""
    core = engine()
    session = recipe_session(recipe, x_offset)
    base = base_data(record) or data_snapshot(record)
    if recipe["source"] == "fid":
        source = find_raw_source(record["source_folder"])
        spectrum, data = processed_from_raw(source, recipe["processing"], record["source_folder"])
    else:
        data = data_snapshot(base)
        order = np.argsort(data["ppm"])
        ppm, y = np.asarray(data["ppm"])[order], current_signal(data)[order]
        step = float(np.median(np.diff(ppm)))
        sf = float(data["spectral_width_hz"]) / (step * len(ppm))
        spectrum = core.Spectrum(
            ppm,
            y,
            y.astype(complex),
            sf,
            float(data["spectral_width_hz"]),
            str(record["source_folder"]),
            (float(data.get("phase0", 0)), float(data.get("phase1", 0))),
            ["Подгонка текущего обработанного сигнала NOX; FFT не повторяется."],
        )
        # Phase and LB settings describe raw processing, not another broadening
        # of an already processed signal. Keep LB out of the fit's initial width.
        session.processing.lb_hz = 0
    if recipe.get("auto_threshold", False):
        region_mask = np.zeros(len(spectrum.ppm), dtype=bool)
        for region in session.regions:
            region_mask |= (spectrum.ppm >= region.low) & (spectrum.ppm <= region.high)
        if np.count_nonzero(region_mask) < 8:
            raise ValueError("Области обработки вне шкалы спектра или содержат слишком мало точек.")
        session.fit.threshold = auto_threshold(spectrum.intensity[region_mask])
    analysis = core.analyze(spectrum, session)
    if analysis.errors:
        raise ValueError(
            "\n".join(
                f"{name}: {explain_fit_message(error)}" for name, error in analysis.errors.items()
            )
        )
    corrected = spectrum.intensity.copy()
    models = []
    for result in analysis.results.values():
        region = next(r for r in session.regions if r.name == result.region)
        mask = (spectrum.ppm >= region.low) & (spectrum.ppm <= region.high)
        corrected[mask] = result.corrected
        models.append(
            dict(
                name=region.name,
                low=region.low,
                high=region.high,
                centers=result.centers.copy(),
                amplitudes=result.amplitudes.copy(),
                widths=result.widths.copy(),
                mixing=np.ones(len(result.centers))
                if result.mixing is None
                else result.mixing.copy(),
                x=result.x.copy(),
                raw=result.raw.copy(),
                baseline=result.baseline.copy(),
                corrected=result.corrected.copy(),
                fitted=result.fitted.copy(),
                diagnostics=copy.deepcopy(result.diagnostics),
                notes=list(result.notes),
                picked_centers=result.centers.copy()
                - np.asarray(
                    result.diagnostics.get("center_shifts_hz", np.zeros(len(result.centers)))
                )
                / spectrum.sf_mhz,
            )
        )
        if result.line_shapes is not None:
            models[-1].update(
                line_shapes=list(result.line_shapes),
                extra_widths=result.extra_widths.copy(),
                profile_phases=result.profile_phases.copy(),
            )
    # Always use ascending axis for the adapter, regardless of NOX's input order.
    order = np.argsort(data["ppm"])
    for key in ("ppm", "real", "imaginary"):
        data[key] = np.asarray(data[key])[order].copy()
    data["corrected"] = corrected
    state = dict(
        recipe=copy.deepcopy(recipe),
        models=models,
        notes=analysis.notes,
        phases=list(spectrum.phases),
        status=analysis.status,
        expected=data_snapshot(data),
        input_snapshot=data_snapshot(base),
        sf_mhz=spectrum.sf_mhz,
        threshold=session.fit.threshold,
    )
    return data, state


def fit_current(record):
    spin = record.get("spin")
    if not spin:
        return False
    expected = spin["expected"]
    if any(record.get(key) != expected.get(key) for key in ("phase0", "phase1")):
        return False
    if not same_state(record.get("corrected"), expected.get("corrected")):
        return False
    delta = float(record.get("ppm_shift", 0)) - float(expected.get("ppm_shift", 0))
    return np.shape(record["ppm"]) == np.shape(expected["ppm"]) and np.allclose(
        record["ppm"], expected["ppm"] + delta, rtol=0, atol=1e-9
    )


def model_component(model, index, x=None):
    """One curve using the saved shape; old projects remain pure Lorentzians."""
    if "line_shapes" in model:
        from spintegrate.profiles import component_curve

        return component_curve(
            model["x"] if x is None else x,
            model["centers"][index],
            model["widths"][index],
            model["amplitudes"][index],
            model["mixing"][index],
            model["line_shapes"][index],
            model["extra_widths"][index],
            model["profile_phases"][index],
        )
    mixing = model.get("mixing")
    return engine().peak_profile(
        model["x"] if x is None else x,
        model["centers"][index],
        model["widths"][index],
        model["amplitudes"][index],
        1.0 if mixing is None else mixing[index],
    )


def model_component_areas(model):
    if "line_shapes" in model:
        from spintegrate.profiles import model_areas

        return model_areas(
            model["widths"],
            model["amplitudes"],
            model["mixing"],
            model["line_shapes"],
            model["extra_widths"],
            model["profile_phases"],
        )
    return engine().peak_area(model["amplitudes"], model["widths"], model.get("mixing", 1.0))


def model_component_fwhms(model):
    if "line_shapes" not in model:
        return 2 * np.asarray(model["widths"])
    from spintegrate.profiles import component_fwhm

    return np.array(
        [
            component_fwhm(w, eta, shape, extra)
            for w, eta, shape, extra in zip(
                model["widths"], model["mixing"], model["line_shapes"], model["extra_widths"]
            )
        ]
    )


def model_area(record, left, right, *, absolute=False, x_offset=0.0):
    """Full areas of lines whose fitted centers lie in one fitted parent region.

    An unfitted/stale/ambiguous interval is NaN, not a convincing false zero.
    Real intervals containing no fitted peaks are legitimate zero integrals.
    """
    if not fit_current(record):
        return float("nan")
    spin = record["spin"]
    delta = float(record.get("ppm_shift", 0)) - float(spin["expected"].get("ppm_shift", 0))
    low, high = sorted((float(left) - x_offset - delta, float(right) - x_offset - delta))
    parents = [m for m in spin["models"] if m["low"] - 1e-9 <= low < high <= m["high"] + 1e-9]
    if len(parents) != 1:
        return float("nan")
    model = parents[0]
    centers = np.asarray(model["centers"])
    mask = (centers >= low) & (centers <= high)
    terms = model_component_areas(model)[mask]
    return float(np.sum(np.abs(terms)) if absolute else np.sum(terms))


MODEL_ARRAYS = ("centers", "amplitudes", "widths", "x", "raw", "baseline", "corrected", "fitted")
OPTIONAL_MODEL_ARRAYS = ("mixing", "extra_widths", "profile_phases", "picked_centers")


def pack_spin(spin):
    if spin is None:
        return None
    result = copy.deepcopy(spin)
    result["expected"] = pack_data(spin["expected"])
    result["input_snapshot"] = pack_data(spin["input_snapshot"])
    for model in result["models"]:
        for field in MODEL_ARRAYS + tuple(key for key in OPTIONAL_MODEL_ARRAYS if key in model):
            model[field] = encode_signal(model[field])
    return result


def unpack_spin(payload):
    if payload is None:
        return None
    result = copy.deepcopy(payload)
    result["expected"] = unpack_data(payload["expected"])
    result["input_snapshot"] = unpack_data(payload["input_snapshot"])
    # Validate the saved recipe, even though reopening does not re-fit data.
    recipe_session(result["recipe"])
    previous_high = -np.inf
    for model in sorted(result["models"], key=lambda m: m["low"]):
        if not previous_high <= float(model["low"]) < float(model["high"]):
            raise ValueError("Некорректные области сохранённой модели.")
        previous_high = float(model["high"])
        for field in MODEL_ARRAYS + tuple(key for key in OPTIONAL_MODEL_ARRAYS if key in model):
            size = int(model[field]["size"])
            model[field] = decode_signal(model[field], size)
            if not np.isfinite(model[field]).all():
                raise ValueError("Модель содержит нечисловые значения.")
        if len({len(model[k]) for k in ("centers", "amplitudes", "widths")}) != 1:
            raise ValueError("Некорректные параметры компонент.")
        if np.any(model["widths"] <= 0):
            raise ValueError("Ширины компонент должны быть положительными.")
        if "picked_centers" in model and len(model["picked_centers"]) != len(model["centers"]):
            raise ValueError("Некорректные исходные центры компонент.")
        if "mixing" in model and (
            len(model["mixing"]) != len(model["centers"])
            or np.any((model["mixing"] < 0) | (model["mixing"] > 1))
        ):
            raise ValueError("Некорректная доля Лоренца в форме линии.")
        if any(k in model for k in ("line_shapes", "extra_widths", "profile_phases")):
            from spintegrate.profiles import LINE_SHAPES

            count = len(model["centers"])
            if (
                not isinstance(model.get("line_shapes"), list)
                or len(model["line_shapes"]) != count
                or any(s not in LINE_SHAPES for s in model["line_shapes"])
                or any(
                    k not in model or len(model[k]) != count
                    for k in ("mixing", "extra_widths", "profile_phases")
                )
                or np.any(model["extra_widths"] <= 0)
                or np.any(np.abs(model["profile_phases"]) > np.deg2rad(30) + 1e-10)
            ):
                raise ValueError("Некорректные параметры гибкой формы линии.")
        if len({len(model[k]) for k in ("x", "raw", "baseline", "corrected", "fitted")}) != 1:
            raise ValueError("Массивы подгонки имеют разную длину.")
    if "groups" in result:
        from spin_groups import validate_groups

        validate_groups(result)
    return result
