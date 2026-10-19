"""Manual-phase queue never changes phase or accepts missing fitted areas as zero."""

import copy
import json

import numpy as np
import pytest

from batch_session import BatchSession
from processing_templates import apply_baseline_steps, apply_template, make_template
from spin_backend import run_selected_region, same_state
from spin_groups import group_area
from test_quantitative_workflow import inputs
from test_spin_bridge import recipe


def template_input():
    record, formula = inputs()
    template = make_template(record, record["integrals"], formula.to_preset(record["integrals"]))
    return record, template


def fitted_input():
    record, formula = inputs()
    state = run_selected_region(record, recipe(), 3.4, 3.7)
    state["groups"] = [
        dict(id="a", name="A", components=[0], missing=0),
        dict(id="b", name="B", components=[1], missing=0),
    ]
    record["spin_analyses"] = {"fit": state}
    for index, item in enumerate(record["integrals"]):
        item.update(left=3.4, right=3.7, spin_fit_id="fit", spin_group_id="ab"[index])
    return record, make_template(record, record["integrals"], formula.to_preset(record["integrals"]))


def test_preserve_operator_phase_and_recalculate_background_without_accumulation():
    record, template = template_input()
    step = dict(method="Адаптивный arPLS", low=3, high=4, polarity="auto", filter_hz=0, smoothness=0)
    template["baseline_steps"] = [step]
    record.update(phase0=17.25, phase1=-3.2, phase_pivot=.37, phase_reference=3.5,
                  corrected=np.full_like(record["real"], 1e9))
    before = copy.deepcopy(record)
    result, info = apply_template(record, template, align=False, phase_mode="preserve")
    for key in ("phase0", "phase1", "phase_pivot", "phase_reference"):
        assert result[key] == record[key]
    assert result["corrected"] == pytest.approx(apply_baseline_steps(record, [step]))
    repeated, _ = apply_template(result, template, align=False, phase_mode="preserve")
    assert repeated["corrected"] == pytest.approx(result["corrected"])
    assert same_state(record, before)
    assert info["phase_mode"] == "preserve"
    assert not info["incomplete_fits"]


def test_preserve_without_baseline_uses_operator_phase_not_previous_corrected_signal():
    record, template = template_input()
    record.update(phase0=42, phase1=-10, phase_pivot=.4, corrected=record["real"] * 3)
    result, _ = apply_template(record, template, align=False, phase_mode="preserve")
    assert result["corrected"] is None
    assert result["phase0"] == 42 and result["phase1"] == -10 and result["phase_pivot"] == .4
    copied, _ = apply_template(record, template, align=False)
    assert copied["phase0"] == template["phase"]["phase0"]


def test_manual_phase_rejects_automatic_phase_and_unknown_modes():
    record, template = template_input()
    with pytest.raises(ValueError, match="несовместима"):
        apply_template(record, template, phase_mode="preserve", auto_phase=True)
    with pytest.raises(ValueError, match="режим фазы"):
        apply_template(record, template, phase_mode="invented")


@pytest.mark.parametrize("field,value", [
    ("imaginary", np.zeros(5)), ("real", np.array([np.nan] * 4001)),
    ("ppm", np.zeros(4001)), ("phase0", np.inf),
])
def test_target_validation_rejects_incompatible_data(field, value):
    record, template = template_input()
    record[field] = value
    with pytest.raises(ValueError):
        apply_template(record, template, align=False, phase_mode="preserve")


def test_incomplete_spin_groups_are_editable_but_never_silent_zero():
    from spintegrate.core import lorentzian

    record, template = fitted_input()
    record["real"] = lorentzian(record["ppm"], 3.58, .009, 6500)
    with pytest.raises(ValueError, match="линии группы"):
        apply_template(record, template, align=False, phase_mode="preserve")
    result, info = apply_template(record, template, align=False, phase_mode="preserve", allow_incomplete=True)
    state = result["spin_analyses"]["fit"]
    assert state["groups"][0]["missing"] == 1
    assert np.isnan(group_area(result, state, "a"))
    assert group_area(result, state, "b") > 0
    assert info["incomplete_fits"] == ["fit"]
    assert info["warnings"]


def test_fit_domain_must_be_covered_by_target():
    record, template = fitted_input()
    template["fits"]["fit"]["low"] = 2.9
    with pytest.raises(ValueError, match="Spintegrate.*пределы"):
        apply_template(record, template, align=False, phase_mode="preserve")


def test_unresolved_numeric_region_and_absent_automatic_fit_are_not_zero():
    record, template = template_input()
    template["integrals"][0].update(left=3.50001, right=3.50002)
    with pytest.raises(ValueError, match="двух точек"):
        apply_template(record, template, align=False, phase_mode="preserve")
    record, template = fitted_input()
    fit = template["fits"]["fit"]
    fit["groups"] = [dict(id="all", name="Whole region", components=[0, 1], missing=0, automatic=True)]
    template["integrals"] = [template["integrals"][0]]
    template["integrals"][0]["spin_group_id"] = "all"
    record["real"] = np.zeros_like(record["real"])
    result, info = apply_template(record, template, align=False, phase_mode="preserve", allow_incomplete=True)
    state = result["spin_analyses"]["fit"]
    assert np.isnan(group_area(result, state, "all"))
    assert state["groups"][0]["missing"] == 2
    assert info["incomplete_fits"]


def test_queue_reference_phase_review_and_confirmation_are_separate():
    _, template = template_input()
    session = BatchSession([11, 13, 22], reference_id=13)
    assert session.spectrum_ids == [13, 11, 22]
    assert session.current_item["status"] == "reference"
    session.next()
    with pytest.raises(ValueError, match="образец"):
        session.begin_processing()
    session.mark_reference_ready(template)
    assert session.current_sid == 11  # accepting the sample never moves selection
    with pytest.raises(ValueError, match="фазу"):
        session.begin_processing()
    session.mark_phase_done(signature={"phase0": 2.5, "phase1": 0})
    token = session.begin_processing()
    assert session.current_item["status"] == "processing"
    assert session.complete_processing(11, dict(warnings=["Check tails"]), revision=token)
    assert session.current_item["status"] == "review"
    session.confirm()
    assert session.current_sid == 11
    assert session.current_item["status"] == "confirmed"
    session.next()
    session.skip()
    assert session.finished
    session.previous()
    assert session.current_item["status"] == "confirmed"


def test_missing_components_block_acceptance_until_operator_rechecks():
    _, template = template_input()
    session = BatchSession([1, 2], template=template)
    session.next()
    session.mark_phase_done()
    token = session.begin_processing()
    session.complete_processing(2, dict(incomplete_fits=["fit"], warnings=["Lost A"]), revision=token)
    with pytest.raises(ValueError, match="Исправьте"):
        session.confirm()
    session.mark_review(diagnostic=dict(incomplete_fits=[], warnings=[]))
    session.confirm()
    assert session.finished


def test_worker_tokens_reject_late_results_after_retry_or_phase_edit():
    _, template = template_input()
    session = BatchSession([1, 2], template=template)
    session.next()
    session.mark_phase_done()
    first = session.begin_processing()
    session.retry()
    second = session.begin_processing()
    assert not session.complete_processing(2, revision=first)
    assert not session.fail_processing(2, "old failure", revision=first)
    session.phase_changed()
    assert not session.complete_processing(2, revision=second)
    assert not session.current_item["phase_confirmed"]


def test_failure_keeps_operator_phase_but_requires_explicit_retry():
    _, template = template_input()
    session = BatchSession([1, 2], template=template)
    session.next()
    session.mark_phase_done()
    token = session.begin_processing()
    assert session.fail_processing(2, "No ROI", revision=token)
    assert session.current_item["status"] == "error"
    assert session.current_item["phase_confirmed"]
    with pytest.raises(ValueError):
        session.confirm()
    session.retry()
    assert session.current_item["status"] == "awaiting_phase"
    session.begin_processing()


def test_template_is_isolated_and_update_only_changes_unaccepted_items():
    _, template = template_input()
    session = BatchSession([1, 2, 3], template=template)
    session.next()
    session.mark_phase_done()
    token = session.begin_processing()
    session.complete_processing(2, revision=token)
    session.confirm()
    accepted = copy.deepcopy(session.current_item)
    session.next()
    session.mark_phase_done()
    token = session.begin_processing()
    changed = copy.deepcopy(template)
    changed["integrals"][0]["left"] += .001
    session.set_template(changed)
    changed["integrals"][0]["left"] += .001
    exposed = session.template
    exposed["integrals"][0]["left"] += .1
    assert session.template["integrals"][0]["left"] == pytest.approx(template["integrals"][0]["left"] + .001)
    assert session.item(2) == accepted
    assert session.item(3)["status"] == "awaiting_phase"
    assert not session.complete_processing(3, revision=token)


def test_paused_queue_never_starts_and_cannot_enable_auto_phase():
    _, template = template_input()
    session = BatchSession([1, 2], template=template)
    session.next()
    session.mark_phase_done()
    session.pause()
    with pytest.raises(ValueError, match="приостановлена"):
        session.begin_processing()
    session.resume()
    session.begin_processing()
    for options in (dict(auto_phase=True), dict(phase_mode="template")):
        with pytest.raises(ValueError, match="оператором"):
            BatchSession([1, 2], options=options)


def test_project_index_mapping_roundtrip_and_interrupted_work_are_safe():
    _, template = template_input()
    session = BatchSession([11, 13, 22], reference_id=13, template=template)
    session.select(11)
    session.mark_phase_done(signature={"phase0": 3})
    session.begin_processing()
    session.pause()
    saved = json.loads(json.dumps(session.to_payload({11: 0, 13: 1, 22: 2})))
    restored = BatchSession.from_payload(saved, {0: 101, 1: 102, 2: 103})
    assert restored.spectrum_ids == [102, 101, 103]
    assert restored.reference_id == 102 and restored.current_sid == 101
    assert restored.paused
    assert restored.current_item["status"] == "awaiting_phase"
    assert not restored.current_item["phase_confirmed"]
    assert restored.current_item["warnings"]
    assert restored.template == template
    assert "real" not in saved and "corrected" not in saved


def test_completed_review_and_formula_survive_project_reload():
    _, template = template_input()
    session = BatchSession([1, 2], template=template)
    session.next()
    session.mark_phase_done()
    session.complete_processing(2, revision=session.begin_processing())
    saved = session.to_payload(include_template=False)
    restored = BatchSession.from_payload(saved, template=template)
    assert restored.current_item["status"] == "review"
    assert restored.formula == template["formula"]
    restored.confirm()
    assert restored.finished


def test_queue_rejects_arrays_signatures_duplicates_and_missing_project_ids():
    with pytest.raises(ValueError):
        BatchSession([1, 1])
    session = BatchSession([1, 2])
    session.next()
    with pytest.raises(ValueError, match="JSON"):
        session.mark_phase_done(signature=np.zeros(2))
    with pytest.raises(ValueError, match="сохраняемый проект"):
        session.to_payload({1: 0})


def test_worker_diagnostic_blockers_are_inherited_unless_explicitly_replaced():
    _, template = template_input()
    session = BatchSession([1, 2], template=template)
    session.next()
    session.mark_phase_done()
    token = session.begin_processing()
    session.complete_processing(2, dict(blocking_issues=["Missing formula input"]), revision=token)
    with pytest.raises(ValueError, match="Missing formula"):
        session.confirm()
    session.mark_review(diagnostic=dict(blocking_issues=["Old issue"]), blocking_issues=[])
    session.confirm()
    assert session.finished


@pytest.mark.parametrize("payload", [None, [], 1, "queue", {"format": "unknown"}])
def test_restore_rejects_non_dict_payload_and_unknown_formats(payload):
    with pytest.raises(ValueError):
        BatchSession.from_payload(payload)


@pytest.mark.parametrize("path,value", [
    (("items",), {}), (("items",), [None]), (("items", 0, "spectrum_id"), []),
    (("items", 0, "status"), []), (("items", 0, "status"), "unknown"),
    (("items", 0, "revision"), True), (("items", 0, "phase_confirmed"), "yes"),
    (("items", 0, "warnings"), "warning"), (("items", 0, "blocking_issues"), [1]),
    (("items", 0, "diagnostic"), []), (("items", 0, "error"), []),
    (("current_id",), []), (("reference_id",), 99), (("formula",), []),
    (("options",), []), (("paused",), "false"), (("template_revision",), "1"),
])
def test_restore_rejects_invalid_item_and_metadata_types(path, value):
    payload = BatchSession([1, 2]).to_payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        BatchSession.from_payload(payload)


def test_restore_rejects_missing_and_aliased_project_mapping():
    payload = BatchSession([1, 2]).to_payload()
    for mapping in ({}, {1: 11}, {1: 11, 2: 11}, {1: 11, 2: []}, []):
        with pytest.raises(ValueError):
            BatchSession.from_payload(payload, mapping)


def test_processed_queue_cannot_reload_without_its_template():
    _, template = template_input()
    payload = BatchSession([1, 2], template=template).to_payload(include_template=False)
    with pytest.raises(ValueError, match="отсутствует шаблон"):
        BatchSession.from_payload(payload)
