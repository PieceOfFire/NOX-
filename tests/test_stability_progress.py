"""Progress callbacks are deterministic; stopped subsets never claim full checks."""

import copy
import threading

import pytest

from integral_stability import (
    analyze_integral_stability,
    integral_stability_case_count,
    summarize_integral_stability,
)
from sensitivity import analyze_sensitivity, sensitivity_case_count, summarize_sensitivity
from spectrum_state import same_state
from test_quantitative_workflow import inputs


def test_integral_progress_counts_every_case_and_item_without_mutation():
    record, _ = inputs()
    before = copy.deepcopy(record)
    progress = []
    rows = analyze_integral_stability(
        record, record["integrals"], progress=lambda *event: progress.append(event)
    )
    assert len(rows) == 26
    assert progress[0][0:2] == (0, 26)
    assert progress[-1][0:2] == (26, 26)
    assert sorted(event[0] for event in progress) == [event[0] for event in progress]
    assert "13/13" in progress[-1][2]
    assert integral_stability_case_count(record, record["integrals"]) == 13
    assert all(
        not s["partial"] and s["completed"] == s["planned"] == 13
        for s in summarize_integral_stability(rows)
    )
    assert same_state(record, before)


@pytest.mark.parametrize("keep_partial", [False, True])
def test_integral_stop_keeps_only_completed_work_when_opted_in(keep_partial):
    record, _ = inputs()
    stop = threading.Event()

    def progress(completed, total, label):
        if completed == 3:
            stop.set()

    rows = analyze_integral_stability(
        record, record["integrals"], stop=stop, progress=progress, keep_partial=keep_partial
    )
    if keep_partial:
        assert len(rows) == 3
        summaries = summarize_integral_stability(rows)
        assert [s["completed"] for s in summaries] == [2, 1]
        assert all(s["partial"] and s["planned"] == 13 for s in summaries)
    else:
        assert rows == []


def test_integral_unchecked_placeholder_is_zero_checks_not_valid_zero():
    summary = summarize_integral_stability(
        [
            dict(
                number=1,
                case="Исходный",
                name="A",
                method="Численный",
                status="cancelled",
                value=None,
                percent=None,
                change=None,
                message="Остановлено",
                planned_cases=13,
                completed_case=False,
            )
        ]
    )[0]
    assert summary["partial"] and summary["completed"] == 0 and summary["planned"] == 13
    assert summary["nominal"] is None and summary["low"] is None and summary["high"] is None


@pytest.mark.parametrize("keep_partial", [False, True])
def test_formula_progress_partial_results_remain_explicit(keep_partial):
    record, doc = inputs()
    stop = threading.Event()
    events = []
    contexts = {1: (record["integrals"][0], 0), 2: (record["integrals"][1], 0)}

    def progress(completed, total, label):
        events.append((completed, total, label))
        if completed == 2:
            stop.set()

    rows = analyze_sensitivity(
        record, doc.blocks, contexts, stop=stop, progress=progress, keep_partial=keep_partial
    )
    assert sensitivity_case_count(record, contexts) == 5
    assert events[0][:2] == (0, 5) and events[-1][:2] == (2, 5)
    if keep_partial:
        summary = summarize_sensitivity(rows)[0]
        assert len(rows) == 2
        assert summary["partial"] and summary["completed"] == 2 and summary["planned"] == 5
    else:
        assert rows == []
