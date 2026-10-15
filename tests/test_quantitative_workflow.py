"""Numerical checks for repeatable processing and honest diagnostics."""

import copy
import json
import threading

import numpy as np
import pytest

from formula_document import FormulaDocument
from integration import integrate_region
from processing_templates import make_template, apply_template, load_template
from report_export import write_report
from sensitivity import analyze_sensitivity, summarize_sensitivity
from signal_quality import fit_quality, integral_quality, noise_sigma
from spin_backend import run_selected_region, same_state
from spin_groups import group_area
from test_spin_bridge import record, recipe


def inputs():
    r = record()
    r.update(
        id=1, name="Sample", integration_method="Обычная площадь", integration_display="Площадь"
    )
    items = [
        dict(id=1, number=1, name="A", color="#e45756", left=3.45, right=3.53),
        dict(id=2, number=2, name="B", color="#4c78a8", left=3.54, right=3.65),
    ]
    r["integrals"] = items
    doc = FormulaDocument(
        blocks={
            1: dict(kind="area", area_number=1, x=0, y=0),
            2: dict(kind="area", area_number=2, x=0, y=100),
            3: dict(kind="operation", operation="÷", inputs=[1, 2], x=200, y=50),
            4: dict(kind="output", input=3, name="Ratio", x=350, y=50),
        }
    )
    return r, doc


def test_noise_and_edge_background_are_scale_consistent():
    rng = np.random.default_rng(4)
    x = np.linspace(-1, 1, 10001)
    y = 50 * np.exp(-((x / 0.05) ** 2)) + 4 + rng.normal(0, 0.2, len(x))
    q = integral_quality(x, y, -0.2, 0.2)
    assert q["noise"] == pytest.approx(0.2, rel=0.15)
    assert q["background_area"] == pytest.approx(1.6, rel=0.03)
    assert q["background_pct"] > 20
    assert q["warnings"]
    scaled = integral_quality(x[::-1], (y * 300)[::-1], -0.2, 0.2)
    assert scaled["area"] == pytest.approx(q["area"] * 300)
    assert scaled["background_pct"] == pytest.approx(q["background_pct"])
    assert noise_sigma(rng.normal(0, 1, 40000)) == pytest.approx(1, rel=0.02)


def test_negative_lobe_is_diagnosed_not_silently_removed():
    x = np.linspace(-1, 1, 4001)
    y = np.exp(-(((x + 0.1) / 0.08) ** 2)) - 0.4 * np.exp(-(((x - 0.1) / 0.08) ** 2))
    q = integral_quality(x, y, -0.5, 0.5)
    absolute = integral_quality(x, y, -0.5, 0.5, absolute=True)
    assert q["cancellation_pct"] > 40
    assert absolute["area"] > q["area"]
    assert not any("Отрицательная" in w for w in absolute["warnings"])


def test_structured_fit_residual_is_flagged():
    x = np.linspace(-1, 1, 5000)
    observed = np.exp(-((x / 0.05) ** 2))
    q = fit_quality(dict(corrected=observed, fitted=observed * 0.8, centers=[0]))
    assert q["autocorrelation"] > 0.9
    assert q["warnings"]


def test_template_roundtrip_alignment_and_no_source_mutation(tmp_path):
    r, doc = inputs()
    original = copy.deepcopy(r)
    template = make_template(r, r["integrals"], doc.to_preset(r["integrals"]))
    path = tmp_path / "template.json"
    path.write_text(json.dumps(template), encoding="utf-8")
    template = load_template(path)
    target = copy.deepcopy(r)
    target["ppm"] += 0.012
    target["real"] *= 0.7
    result, info = apply_template(target, template)
    assert info["shift"] == pytest.approx(-0.012, abs=0.0002)
    assert result["ppm"] == pytest.approx(r["ppm"], abs=0.0002)
    assert result["integrals"] == r["integrals"]
    assert same_state(original, r)
    assert target["ppm"] == pytest.approx(r["ppm"] + 0.012)


def test_template_rejects_unknown_baseline_and_reproduces_known_correction():
    r, doc = inputs()
    r["corrected"] = r["real"].copy()
    with pytest.raises(ValueError, match="старой базовой линии"):
        make_template(r, r["integrals"], doc.to_preset(r["integrals"]))
    r["baseline_steps"] = [
        dict(method="Адаптивный arPLS", low=3, high=4, polarity="auto", filter_hz=0, smoothness=0)
    ]
    template = make_template(r, r["integrals"], doc.to_preset(r["integrals"]))
    result, _ = apply_template(r, template, align=False)
    assert np.isfinite(result["corrected"]).all()
    assert result["baseline_steps"] == r["baseline_steps"]


def test_template_refits_named_groups_and_rejects_lost_lines():
    r, doc = inputs()
    state = run_selected_region(r, recipe(), 3.4, 3.7)
    state["groups"] = [
        dict(id="a", name="A", components=[0], missing=0),
        dict(id="b", name="B", components=[1], missing=0),
    ]
    r["spin_analyses"] = {"fit": state}
    for index, item in enumerate(r["integrals"]):
        item.update(left=3.4, right=3.7, spin_fit_id="fit", spin_group_id="ab"[index])
    template = make_template(r, r["integrals"], doc.to_preset(r["integrals"]))
    target = copy.deepcopy(r)
    target["real"] *= 0.8
    result, _ = apply_template(target, template, align=False)
    assert group_area(result, result["spin_analyses"]["fit"], "a") == pytest.approx(
        np.pi * 72 * 0.8, rel=0.02
    )
    target["real"] = np.zeros_like(target["real"])
    with pytest.raises(ValueError, match="линии группы"):
        apply_template(target, template, align=False)


def test_template_whole_area_remains_dynamic_when_peak_set_changes():
    from spintegrate.core import lorentzian

    r, _ = inputs()
    fit = run_selected_region(r, recipe(), 3.4, 3.7)
    fit["groups"] = [
        dict(id="all", name="Вся область", components=[0, 1], missing=0, automatic=True)
    ]
    r["spin_analyses"] = {"fit": fit}
    r["integrals"] = [r["integrals"][0]]
    r["integrals"][0].update(left=3.4, right=3.7, spin_fit_id="fit", spin_group_id="all")
    template = make_template(r, r["integrals"], FormulaDocument().to_preset(r["integrals"]))
    target = copy.deepcopy(r)
    target["real"] = lorentzian(target["ppm"], 3.63, 0.006, 9000.0)
    result, _ = apply_template(target, template, align=False)
    state = result["spin_analyses"]["fit"]
    assert state["groups"][0]["components"] == [0]
    assert state["groups"][0]["missing"] == 0
    assert state["groups"][0]["automatic"]
    assert group_area(result, state, "all") == pytest.approx(np.pi * 9000 * 0.006, rel=0.01)


def test_sensitivity_uses_formula_graph_and_does_not_mutate_inputs():
    r, doc = inputs()
    before = copy.deepcopy(r)
    contexts = {1: (r["integrals"][0], 0), 2: (r["integrals"][1], 0)}
    rows = analyze_sensitivity(r, doc.blocks, contexts, delta=0.002)
    assert {row["case"] for row in rows} == {
        "Исходный",
        "Границы шире",
        "Границы уже",
        "Фон +σ",
        "Фон −σ",
    }
    summary = summarize_sensitivity(rows)[0]
    assert summary["low"] < summary["high"]
    assert summary["percent"] > 0
    assert summary["failed"] == 0
    assert same_state(before, r)


def test_report_is_self_contained_and_escapes_labels(tmp_path):
    r, doc = inputs()
    r["name"] = "<script>alert(1)</script>"
    path = tmp_path / "report.html"
    write_report(
        path,
        [
            dict(
                record=r,
                items=r["integrals"],
                offset=0,
                method="Обычная площадь",
                display="Площадь",
                integrals=[[1, "A", "3.45 … 3.53", 1.0, 1.0, "Численный", "Готово"]],
                outputs=[["Ratio", 1.2, "Готово"]],
            )
        ],
        doc.to_preset(r["integrals"]),
    )
    html = path.read_text(encoding="utf-8")
    assert "<svg" in html and "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "Формула для воспроизведения" in html


def test_repeated_formula_source_is_perturbed_once():
    r, doc = inputs()
    doc.blocks[3]["operation"] = "+"
    item = r["integrals"][0]
    rows = analyze_sensitivity(r, doc.blocks, {1: (item, 0), 2: (item, 0)}, delta=0.002)
    wider = next(row for row in rows if row["case"] == "Границы шире")
    expected = 2 * integrate_region(
        r["ppm"], r["real"], item["left"] - 0.002, item["right"] + 0.002
    )
    assert wider["value"] == pytest.approx(expected)


def test_fitted_sensitivity_and_stale_model_are_explicit():
    r, doc = inputs()
    state = run_selected_region(r, recipe(), 3.4, 3.7)
    state["groups"] = [
        dict(id="a", name="A", components=[0], missing=0),
        dict(id="b", name="B", components=[1], missing=0),
    ]
    r["spin_analyses"] = {"fit": state}
    for index, item in enumerate(r["integrals"]):
        item.update(left=3.4, right=3.7, spin_fit_id="fit", spin_group_id="ab"[index])
    contexts = {1: (r["integrals"][0], 0), 2: (r["integrals"][1], 0)}
    rows = analyze_sensitivity(r, doc.blocks, contexts)
    assert len(rows) == 7 and all(row["status"] == "ready" for row in rows)
    assert rows[0]["value"] == pytest.approx(72 / 58.5, rel=0.02)
    r["phase0"] = 10
    rows = analyze_sensitivity(r, doc.blocks, contexts)
    assert all(row["status"] == "error" and row["value"] is None for row in rows)


def test_cancelled_report_does_not_write_a_file(tmp_path):
    stop = threading.Event()
    stop.set()
    path = tmp_path / "cancelled.html"
    assert not write_report(path, [], {}, stop=stop)
    assert not path.exists()
