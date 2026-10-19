"""Regression replay must recalculate data, and must not alter personal projects."""

import copy
import json

import numpy as np
import pytest

import real_spectrum_checks as checks
from base_line import load_bruker_spectrum
from formula_document import FormulaDocument
from processing_templates import apply_baseline_steps
from project_store import write_project
from spin_backend import (
    data_snapshot,
    default_recipe,
    pack_data,
    pack_spin,
    run_selected_region,
)


@pytest.fixture
def saved_project(tmp_path):
    source = tmp_path / "experiment" / "pdata" / "1"
    source.mkdir(parents=True)
    count = 2048
    x = 6.0 - np.arange(count) / count
    y = 1200 / (1 + ((x - 5.6) / 0.007) ** 2)
    y += 900 / (1 + ((x - 5.4) / 0.009) ** 2) + 20 + 3 * x
    np.rint(y).astype("<i4").tofile(source / "1r")
    np.full(count, 7, dtype="<i4").tofile(source / "1i")
    (source / "procs").write_text(
        f"##$SI= {count}\n##$OFFSET= 6\n##$SW_p= 400\n##$SF= 400\n",
        encoding="ascii",
    )
    ppm, real, imaginary, width, _ = load_bruker_spectrum(source)
    steps = [dict(method="Адаптивный arPLS", low=float(ppm.min()), high=6.0, polarity="auto")]
    record = dict(
        ppm=ppm,
        real=real,
        imaginary=imaginary,
        spectral_width_hz=width,
        phase0=1.5,
        phase1=-0.8,
        phase_pivot=0.5,
        phase_reference=None,
        corrected=None,
        ppm_shift=0.0,
        source_folder=str(source),
        title="Synthetic",
        name="Synthetic",
        baseline_steps=steps,
    )
    record["corrected"] = apply_baseline_steps(record, steps)
    recipe = default_recipe()
    recipe["baseline_default"]["method"] = "none"
    recipe.update(baseline_policy="manual")
    recipe["fit"].update(threshold=100, initial_width_hz=5, max_width_hz=15, restarts=1)
    state = run_selected_region(record, recipe, 5.2, 5.8)
    assert len(state["models"][0]["centers"]) == 2
    state["groups"] = [dict(id="g1", name="Pair", components=[0, 1], missing=0)]
    integrals = [
        dict(
            id=1, number=1, name="Pair", left=5.2, right=5.8, spin_fit_id="f1", spin_group_id="g1"
        ),
        dict(id=2, number=2, name="First", left=5.5, right=5.7),
    ]
    document = FormulaDocument(
        blocks={
            1: dict(kind="area", area_number=1),
            2: dict(kind="area", area_number=2),
            3: dict(kind="operation", operation="÷", inputs=[1, 2]),
            4: dict(kind="output", input=3, name="Area ratio"),
        }
    )
    project = dict(
        format="nmr-analysis-project",
        version=3,
        application="himera",
        spectra=[
            dict(
                name="Synthetic",
                source_folder=str(source),
                integrals=integrals,
                integration_method="Обычная площадь",
                integration_display="Площадь",
                himera=dict(
                    version=2,
                    data=pack_data(data_snapshot(record)),
                    baseline_steps=steps,
                    spin_analyses={"f1": pack_spin(state)},
                ),
            )
        ],
        formula_workspace=document.to_preset(integrals),
        comparisons=[],
        calculation_comparison_index=None,
    )
    path = tmp_path / "sample.nmrproj.json"
    write_project(path, project)
    return path, project


def test_replay_recalculates_fit_and_preserves_sources(saved_project, tmp_path, monkeypatch):
    path, _ = saved_project
    original_fingerprints = checks.input_fingerprints(path)
    reference = tmp_path / "reference.json"
    snapshot = checks.initialize(path, reference)
    result = snapshot["results"]["0"]
    assert result["fits"]["f1"]["line_count"] == 2
    assert result["fits"]["f1"]["groups"]["g1"]["line_count"] == 2
    assert result["formulas"]["4"]["status"] == "ready"
    assert result["formulas"]["4"]["value"] == pytest.approx(
        result["integrals"]["1"] / result["integrals"]["2"]
    )
    assert checks.check_reference(reference)["passed"]
    assert checks.input_fingerprints(path) == original_fingerprints

    actual_solver = checks.run_selected_region
    calls = []

    def changed_solver(*args, **kwargs):
        state = actual_solver(*args, **kwargs)
        calls.append(True)
        state["models"][0]["amplitudes"] *= 1.01
        state["models"][0]["fitted"] *= 1.01
        return state

    monkeypatch.setattr(checks, "run_selected_region", changed_solver)
    report = checks.check_reference(reference)
    assert calls
    assert not report["passed"]
    assert any("integrals.1" in difference for difference in report["differences"])
    assert any("formulas.4.value" in difference for difference in report["differences"])
    assert checks.input_fingerprints(path) == original_fingerprints


def test_baseline_is_replayed_not_loaded_from_saved_corrected(saved_project, monkeypatch):
    path, _ = saved_project
    before = checks.replay_project(path)
    baseline = checks.apply_baseline_steps
    monkeypatch.setattr(checks, "apply_baseline_steps", lambda *args: baseline(*args) - 0.25)
    after = checks.replay_project(path)
    assert np.asarray(before["0"]["working_signal"]) - np.asarray(
        after["0"]["working_signal"]
    ) == pytest.approx(0.25)
    assert any("nox_baseline" in d for d in checks.compare_results(before, after))


@pytest.mark.parametrize("change", ["project", "source"])
def test_input_changes_require_new_reference(saved_project, tmp_path, change):
    path, project = saved_project
    reference = tmp_path / "reference.json"
    checks.initialize(path, reference)
    if change == "project":
        project["spectra"][0]["himera"]["data"]["phase0"] += 1
        write_project(path, project)
    else:
        source = next(source for source in checks.input_fingerprints(path) if source.endswith("1r"))
        with open(source, "ab") as stream:
            stream.write(b"trailing change")
    with pytest.raises(ValueError, match="Изменились входные"):
        checks.check_reference(reference)


def test_unknown_old_baseline_cannot_claim_reproducibility(saved_project):
    path, project = saved_project
    project["spectra"][0]["himera"]["baseline_source_unknown"] = True
    write_project(path, project)
    with pytest.raises(ValueError, match="параметры прежнего фона неизвестны"):
        checks.replay_project(path)


def test_unrelated_source_cannot_be_silently_substituted(saved_project):
    path, project = saved_project
    encoded = project["spectra"][0]["himera"]["data"]
    from spin_backend import unpack_data

    record = unpack_data(encoded)
    record["real"][0] += 1
    project["spectra"][0]["himera"]["data"] = pack_data(record)
    write_project(path, project)
    with pytest.raises(ValueError, match="не совпадает с Bruker"):
        checks.replay_project(path)


def test_comparison_offset_and_physical_formula_units(saved_project):
    path, project = saved_project
    project["comparisons"] = [
        dict(
            spectrum_indices=[0],
            offsets_by_index=[0.01],
            integrals=[dict(id=1, number=1, name="Shifted", left=5.51, right=5.71)],
        )
    ]
    project["calculation_comparison_index"] = 0
    project["formula_workspace"] = FormulaDocument(
        blocks={
            1: dict(kind="area", area_number=1),
            2: dict(kind="output", input=1),
        }
    ).to_preset()
    write_project(path, project)
    first = checks.replay_project(path)["0"]
    assert first["comparison_integrals"]["1"] == pytest.approx(first["integrals"]["2"])
    assert first["formulas"]["2"]["value"] == pytest.approx(first["integrals"]["2"])
    project["spectra"][0]["integration_display"] = "Сумма дискретных точек"
    write_project(path, project)
    assert checks.replay_project(path)["0"]["formulas"] == first["formulas"]


def test_private_paths_tolerances_and_existing_reference(saved_project, tmp_path):
    path, _ = saved_project
    with pytest.raises(ValueError, match="вне папки Himera"):
        checks.initialize(path, checks.APPLICATION_DIR / "private.json")
    with pytest.raises(ValueError, match="Допуски"):
        checks.initialize(path, tmp_path / "bad.json", rtol=float("nan"))
    reference = tmp_path / "reference.json"
    checks.initialize(path, reference)
    original = reference.read_bytes()
    with pytest.raises(ValueError, match="уже существует"):
        checks.initialize(path, reference)
    assert reference.read_bytes() == original


def test_legacy_detector_is_explicit_and_does_not_override_new_recipes(saved_project, tmp_path):
    path, project = saved_project
    default = checks.replay_project(path)["0"]["fits"]["f1"]["peak_detection"]
    assert default == "prominence"
    # An existing detector is authoritative; the option applies only to old recipes.
    assert (
        checks.replay_project(path, legacy_peak_detection="height")["0"]["fits"]["f1"][
            "peak_detection"
        ]
        == "prominence"
    )
    fit = project["spectra"][0]["himera"]["spin_analyses"]["f1"]
    fit["recipe"]["fit"].pop("peak_detection")
    write_project(path, project)
    reference = tmp_path / "legacy.json"
    snapshot = checks.initialize(path, reference, legacy_peak_detection="height")
    assert snapshot["replay_options"] == {"legacy_peak_detection": "height"}
    assert snapshot["results"]["0"]["fits"]["f1"]["peak_detection"] == "height"
    assert checks.check_reference(reference)["passed"]


def test_comparison_reports_arrays_counts_and_formula_status():
    old = dict(curve=[1.0, 2.0], count=2, formula=dict(value=3.0, status="ready"))
    within = copy.deepcopy(old)
    within["curve"][0] += 1e-6
    assert not checks.compare_results(old, within)
    new = copy.deepcopy(old)
    new["curve"][0] += 0.01
    new["count"] = 3
    new["formula"]["status"] = "warning"
    differences = checks.compare_results(old, new)
    assert len(differences) == 3
    assert any("max |Δ|" in d for d in differences)
    assert checks.compare_results(old, {**old, "other": 1})


def test_cli_exit_codes_and_report(saved_project, tmp_path):
    path, _ = saved_project
    reference, report = tmp_path / "reference.json", tmp_path / "report.json"
    assert checks.main(["init", str(path), "--reference", str(reference)]) == 0
    assert checks.main(["check", str(reference), "--report", str(report)]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["passed"]
    assert checks.main(["init", str(path), "--reference", str(reference)]) == 2
    original = path.read_bytes()
    assert checks.main(["check", str(reference), "--report", str(path)]) == 2
    assert path.read_bytes() == original
