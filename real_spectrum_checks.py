r"""Private numerical regression snapshots; these are not scientific ground truth.

Run from PowerShell (reference/output files must be outside the application folder)::

    .\.venv\Scripts\python.exe real_spectrum_checks.py init PROJECT --reference C:\\path\\reference.json
    .\.venv\Scripts\python.exe real_spectrum_checks.py check C:\\path\\reference.json

The project is read only. Each run reloads Bruker data, reapplies saved phase and
NOX baseline steps, refits Spintegrate regions, remaps groups, and evaluates the
saved formula graph. Old saved curves are never used as newly calculated results.
References stay local and may contain private spectra and numerical results.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import math
import platform
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from base_line import load_bruker_spectrum, phase_correct
from formula_document import FormulaDocument
from formula_engine import FormulaEngine
from integration import integrate_region
from processing_templates import apply_baseline_steps
from project_store import read_project
from spin_backend import (
    current_signal,
    fit_current,
    model_component_areas,
    run_selected_region,
    selected_region_area,
    unpack_data,
    unpack_spin,
)
from spin_groups import group_area, remap_groups

FORMAT = "himera-private-regression"
APPLICATION_DIR = Path(__file__).resolve().parent


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_fingerprints(project_path):
    """Changes of data/settings must not masquerade as code regressions."""
    path = Path(project_path).resolve()
    project = read_project(path)
    sources = {str(path): file_hash(path)}
    for spectrum in project.get("spectra", []):
        _, _, _, _, one_r = load_bruker_spectrum(spectrum["source_folder"])
        candidates = [one_r, one_r.parent / "procs", one_r.parent / "1i"]
        if one_r.parent.parent.name == "pdata":
            candidates.append(one_r.parent.parent.parent / "acqus")
        for source in candidates:
            if source.is_file():
                sources[str(source.resolve())] = file_hash(source)
    return sources


def _finite(value, label):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label}: значение не определено; проверьте подгонку и состав групп.")
    return number


def _load_record(payload, *, legacy_peak_detection=None):
    extra = payload.get("himera", {})
    saved = unpack_data(extra.get("data"))
    if saved.get("corrected") is not None and (
        extra.get("baseline_source_unknown") or not extra.get("baseline_steps")
    ):
        raise ValueError(
            f"{payload['name']}: параметры прежнего фона неизвестны. "
            "Для воспроизводимого теста заново примените коррекцию и сохраните проект."
        )
    ppm, real, imaginary, width, one_r = load_bruker_spectrum(payload["source_folder"])
    shift = float(saved.get("ppm_shift") or 0)
    if not (
        np.array_equal(real, saved["real"])
        and np.array_equal(imaginary, saved["imaginary"])
        and np.shape(ppm) == np.shape(saved["ppm"])
        and np.allclose(ppm + shift, saved["ppm"], rtol=0, atol=1e-9)
    ):
        raise ValueError(
            f"{payload['name']}: встроенный сигнал не совпадает с Bruker 1r/1i. "
            "Проверка этого источника/FID пока не поддерживается."
        )
    record = {
        **copy.deepcopy(saved),
        "name": str(payload["name"]),
        "source_folder": str(one_r.parent),
        "spectral_width_hz": width,
        "ppm": ppm + shift,
        "real": real,
        "imaginary": imaginary,
        "baseline_steps": copy.deepcopy(extra.get("baseline_steps", [])),
        "baseline_source_unknown": False,
        "integrals": copy.deepcopy(payload.get("integrals", [])),
        "integration_method": payload.get("integration_method", "Обычная площадь"),
        "spin_analyses": {},
    }
    if record["integration_method"] not in ("Обычная площадь", "Сумма модулей относительно 0"):
        raise ValueError("Для регрессии выберите обычное интегрирование или сумму модулей.")
    phased = phase_correct(
        real, imaginary, record["phase0"], record["phase1"], record["phase_pivot"]
    )
    record["corrected"] = apply_baseline_steps(record, record["baseline_steps"])
    working = current_signal(record)
    measurements = {
        "phase": [record["phase0"], record["phase1"], record["phase_pivot"]],
        "phased_signal": phased.tolist(),
        "nox_baseline": (phased - working).tolist(),
        "working_signal": working.tolist(),
        "fits": {},
    }
    original = {**saved, "source_folder": record["source_folder"]}
    for fit_id, encoded in extra.get("spin_analyses", {}).items():
        old = unpack_spin(encoded)
        if len(old["models"]) != 1:
            raise ValueError("Старые подгонки с несколькими областями пока не поддерживаются.")
        if not fit_current({**original, "spin": old}):
            raise ValueError(f"{record['name']}: сохранённая подгонка {fit_id} устарела.")
        model = old["models"][0]
        delta = shift - float(old["expected"].get("ppm_shift") or 0)
        recipe = copy.deepcopy(old["recipe"])
        if legacy_peak_detection is not None:
            # Early projects predate the prominence detector. The operator can
            # explicitly preserve their old height-threshold semantics.
            recipe["fit"].setdefault("peak_detection", legacy_peak_detection)
        for anchor in recipe["fit"].get("component_shapes", []):
            anchor["center"] += delta
        state = run_selected_region(record, recipe, model["low"] + delta, model["high"] + delta)
        remap_groups(old, state)
        if any(group.get("missing", 0) for group in state.get("groups", [])):
            legacy_note = (
                " В старом рецепте не указан детектор: для прежней семантики высоты "
                "можно явно добавить --legacy-peak-detection height."
                if "peak_detection" not in old["recipe"]["fit"]
                else ""
            )
            raise ValueError(
                f"{record['name']}: после пересчёта потеряны компоненты группы.{legacy_note}"
            )
        record["spin_analyses"][fit_id] = state
        fitted = state["models"][0]
        measurements["fits"][fit_id] = {
            "line_count": len(fitted["centers"]),
            "peak_detection": recipe["fit"].get("peak_detection", "prominence"),
            "centers": fitted["centers"].tolist(),
            "component_areas": model_component_areas(fitted).tolist(),
            "local_baseline": fitted["baseline"].tolist(),
            "fitted_signal": fitted["fitted"].tolist(),
            "r_squared": fitted.get("diagnostics", {}).get("r_squared"),
            "groups": {
                group["id"]: {
                    "name": group["name"],
                    "line_count": len(group["components"]),
                    "area": _finite(group_area(record, state, group["id"]), group["name"]),
                }
                for group in state.get("groups", [])
            },
        }
    return record, measurements


def _area(record, item, offset=0.0):
    absolute = record["integration_method"] == "Сумма модулей относительно 0"
    fit_id = item.get("spin_fit_id")
    if fit_id:
        state = record["spin_analyses"].get(fit_id)
        if state is None:
            raise ValueError(f"Нет подгонки {fit_id} для интеграла {item.get('number')}.")
        if item.get("spin_group_id"):
            value = group_area(record, state, item["spin_group_id"], absolute=absolute)
        else:
            value = selected_region_area(
                record, state, item["left"], item["right"], absolute=absolute, x_offset=offset
            )
    else:
        value = integrate_region(
            record["ppm"],
            current_signal(record),
            item["left"],
            item["right"],
            absolute=absolute,
            x_offset=offset,
        )
    return _finite(value, f"I{item.get('number')}")


def replay_project(project_path, *, legacy_peak_detection=None):
    """Compute phase, baselines, fits, physical areas and saved formula outputs."""
    project = read_project(project_path)
    if not project.get("spectra"):
        raise ValueError("Нужен проект Himera со списком спектров и сохранёнными параметрами.")
    records, results = [], {}
    for index, payload in enumerate(project["spectra"]):
        record, result = _load_record(payload, legacy_peak_detection=legacy_peak_detection)
        records.append(record)
        result["name"] = record["name"]
        result["integrals"] = {
            str(item["number"]): _area(record, item) for item in record["integrals"]
        }
        results[str(index)] = result
    comparison_index = project.get("calculation_comparison_index")
    comparison = None
    if comparison_index is not None:
        comparison = project["comparisons"][comparison_index]
    formulas = project.get("formula_workspace")
    document = FormulaDocument.from_preset(formulas) if formulas else FormulaDocument()
    for index, record in enumerate(records):
        items, offset = record["integrals"], 0.0
        if comparison and index in comparison["spectrum_indices"]:
            position = comparison["spectrum_indices"].index(index)
            items = comparison.get("integrals", [])
            offsets = comparison.get("offsets_by_index", [])
            if len(offsets) != len(comparison["spectrum_indices"]):
                raise ValueError("В сравнении нет воспроизводимых смещений offsets_by_index.")
            offset = float(offsets[position])
            results[str(index)]["comparison_integrals"] = {
                str(item["number"]): _area(record, item, offset) for item in items
            }

        def provider(block):
            item = next((i for i in items if i.get("number") == block.get("area_number")), None)
            if item is not None:
                return _area(record, item, offset)
            # The UI also permits a spectrum-local source when a comparison
            # does not contain the requested integral number.
            item = next(
                (i for i in record["integrals"] if i.get("number") == block.get("area_number")),
                None,
            )
            return _area(record, item) if item is not None else None

        results[str(index)]["formulas"] = {
            str(bid): {"name": name, "value": result.value, "status": result.status}
            for bid, name, result in FormulaEngine(document.blocks, provider).outputs()
        }
    return results


def compare_results(expected, actual, *, rtol=1e-4, atol=1e-6, path="results"):
    """Return concise differences; every numerical array is checked, not only totals."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        if expected.keys() != actual.keys():
            return [f"{path}: изменился набор полей/спектров/интегралов"]
        differences = []
        for key in expected:
            differences.extend(
                compare_results(
                    expected[key], actual[key], rtol=rtol, atol=atol, path=f"{path}.{key}"
                )
            )
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{path}: изменилось число значений ({len(expected)} → {len(actual)})"]
        if all(type(v) in (int, float) for v in expected + actual):
            old, new = np.asarray(expected), np.asarray(actual)
            if not np.allclose(old, new, rtol=rtol, atol=atol):
                return [f"{path}: max |Δ| = {np.max(np.abs(old - new)):.6g}"]
            return []
    if type(expected) is float and type(actual) in (int, float):
        if math.isclose(expected, actual, rel_tol=rtol, abs_tol=atol):
            return []
    elif expected == actual:
        return []
    return [f"{path}: {expected!r} → {actual!r}"]


def _private_path(path):
    path = Path(path).resolve()
    if path.is_relative_to(APPLICATION_DIR):
        raise ValueError(
            "Сохраните личный эталон/отчёт вне папки Himera, например C:\\NSU\\TBC\\tmp."
        )
    return path


def initialize(project_path, reference_path, *, rtol=1e-4, atol=1e-6, legacy_peak_detection=None):
    """Establish current-code behavior; never overwrite an existing reference."""
    reference = _private_path(reference_path)
    if reference.exists():
        raise ValueError("Эталон уже существует. Выберите новое имя для новой версии.")
    if not math.isfinite(rtol) or not math.isfinite(atol) or rtol < 0 or atol < 0:
        raise ValueError("Допуски должны быть конечными и неотрицательными.")
    if legacy_peak_detection not in (None, "height", "prominence"):
        raise ValueError("Неизвестный детектор для старых рецептов.")
    fingerprints = input_fingerprints(project_path)
    replay_options = {"legacy_peak_detection": legacy_peak_detection}
    results = replay_project(project_path, **replay_options)
    if fingerprints != input_fingerprints(project_path):
        raise ValueError("Входной проект/спектр изменился во время расчёта.")
    snapshot = {
        "format": FORMAT,
        "version": 1,
        "kind": "snapshot regression; NOT externally validated conversion",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "project": str(Path(project_path).resolve()),
        "fingerprints": fingerprints,
        "tolerances": {"rtol": rtol, "atol": atol},
        "replay_options": replay_options,
        "runtime": {
            "python": platform.python_version(),
            **{
                name: importlib.metadata.version(name)
                for name in ("numpy", "scipy", "nmrglue", "pybaselines")
            },
        },
        "results": results,
    }
    with reference.open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False, allow_nan=False)
    return snapshot


def check_reference(reference_path):
    with Path(reference_path).open(encoding="utf-8") as stream:
        reference = json.load(stream)
    if reference.get("format") != FORMAT or reference.get("version") != 1:
        raise ValueError("Неизвестный формат локальной проверки.")
    fingerprints = input_fingerprints(reference["project"])
    if fingerprints != reference["fingerprints"]:
        raise ValueError("Изменились входные данные или настройки проекта. Нужен новый эталон.")
    actual = replay_project(reference["project"], **reference.get("replay_options", {}))
    if fingerprints != input_fingerprints(reference["project"]):
        raise ValueError("Входной проект/спектр изменился во время расчёта.")
    differences = compare_results(reference["results"], actual, **reference["tolerances"])
    return {"passed": not differences, "differences": differences, "results": actual}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Локальные проверки воспроизводимости Himera.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Создать личный снимок текущих результатов.")
    init.add_argument("project", type=Path)
    init.add_argument("--reference", type=Path, required=True)
    init.add_argument(
        "--legacy-peak-detection",
        choices=("height", "prominence"),
        help="Явный детектор только для старых рецептов, в которых он ещё не сохранялся.",
    )
    init.add_argument(
        "--rtol", type=float, default=1e-4, help="Относительный допуск: 0.0001 = 0.01%%."
    )
    init.add_argument(
        "--atol", type=float, default=1e-6, help="Абсолютный допуск в физических единицах."
    )
    check = commands.add_parser(
        "check", help="Заново обработать спектры и сравнить с личным снимком."
    )
    check.add_argument("reference", type=Path)
    check.add_argument("--report", type=Path, help="Локальный JSON с результатами сравнения.")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            snapshot = initialize(
                args.project,
                args.reference,
                rtol=args.rtol,
                atol=args.atol,
                legacy_peak_detection=args.legacy_peak_detection,
            )
            print(f"Создан локальный снимок: {args.reference}")
            results = snapshot["results"]
            code = 0
        else:
            report_path = _private_path(args.report) if args.report else None
            report = check_reference(args.reference)
            if report_path:
                with report_path.open("x", encoding="utf-8") as stream:
                    json.dump(report, stream, ensure_ascii=False, allow_nan=False)
            print(
                "PASS: результаты воспроизводятся."
                if report["passed"]
                else "FAIL: результаты изменились."
            )
            for difference in report["differences"][:30]:
                print(difference)
            results = report["results"]
            code = 0 if report["passed"] else 1
        for result in results.values():
            lines = sum(fit["line_count"] for fit in result["fits"].values())
            print(f"{result['name']}: линий {lines}, интегралы {result['integrals']}")
            if result["formulas"]:
                print(f"  Формулы: {result['formulas']}")
            else:
                print("  Формулы отсутствуют: конверсия не вычислялась.")
        print(
            "Это проверка повторяемости кода, а не подтверждение научной точности или эталонной конверсии."
        )
        return code
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"ERROR: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
