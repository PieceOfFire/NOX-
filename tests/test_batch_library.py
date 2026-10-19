"""Queue visibility/navigation and shortcuts use no private spectra or desktop."""

from types import SimpleNamespace

import pytest

from batch_workflow import BatchWorkflowMixin
from spectrum_library import matches_batch_filter, next_attention_id


@pytest.mark.parametrize(
    "status,keys",
    [
        ("reference", {"all", "queue", "waiting"}),
        ("awaiting_phase", {"all", "queue", "waiting"}),
        ("processing", {"all", "queue", "waiting"}),
        ("review", {"all", "queue", "review"}),
        ("confirmed", {"all", "queue", "confirmed"}),
        ("skipped", {"all", "queue", "skipped"}),
        ("error", {"all", "queue", "errors"}),
    ],
)
def test_filters_are_explicit_statuses_not_inferred_processing(status, keys):
    for key in ("all", "queue", "waiting", "review", "confirmed", "skipped", "errors"):
        assert matches_batch_filter({"status": status}, key) == (key in keys)
    assert matches_batch_filter(None, "all")
    assert not matches_batch_filter(None, "queue")


def test_incomplete_review_in_errors_and_review_filters():
    item = {"status": "review", "blocking_issues": ["Потеряна линия"]}
    assert matches_batch_filter(item, "errors")
    assert matches_batch_filter(item, "review")
    assert not matches_batch_filter({"status": "review", "warnings": ["Проверьте фон"]}, "errors")


def test_next_attention_wraps_preserves_order_and_includes_skipped():
    items = [
        {"spectrum_id": 10, "status": "confirmed"},
        {"spectrum_id": 20, "status": "review"},
        {"spectrum_id": 30, "status": "processing"},
        {"spectrum_id": 40, "status": "skipped"},
        {"spectrum_id": 50, "status": "error"},
    ]
    assert next_attention_id(items, 10) == 20
    assert next_attention_id(items, 20) == 40
    assert next_attention_id(items, 50) == 20
    assert next_attention_id(items, None) == 20
    assert next_attention_id(items[:1], 10) is None
    assert next_attention_id([], None) is None


def shortcut_fixture(status="review", widget_class="Treeview"):
    calls = []
    app = SimpleNamespace(
        batch_session=SimpleNamespace(current_item={"status": status}),
        _batch_running=False,
        _spectrum_dialog_open=False,
        spectrum_tab="spectrum",
        notebook=SimpleNamespace(select=lambda: "spectrum"),
        grab_current=lambda: None,
        status=SimpleNamespace(set=lambda value: calls.append(("status", value))),
        batch_action=lambda: calls.append("confirm"),
        batch_skip=lambda: calls.append("skip"),
        batch_step=lambda direction: calls.append(direction),
    )
    widget = SimpleNamespace(winfo_toplevel=lambda: app, winfo_class=lambda: widget_class)
    app.focus_get = lambda: widget
    return app, SimpleNamespace(widget=widget), calls


@pytest.mark.parametrize(
    "widget_class", ["Entry", "TEntry", "Spinbox", "TSpinbox", "Text", "TCombobox"]
)
def test_shortcuts_never_submit_from_text_fields(widget_class):
    app, event, calls = shortcut_fixture(widget_class=widget_class)
    assert BatchWorkflowMixin._batch_shortcut(app, event, "confirm") is None
    assert calls == []


def test_confirm_shortcut_does_not_confirm_phase_or_save_reference():
    for status in ("awaiting_phase", "reference", "processing", "error", "skipped"):
        app, event, calls = shortcut_fixture(status=status)
        assert BatchWorkflowMixin._batch_shortcut(app, event, "confirm") == "break"
        assert "confirm" not in calls
        assert calls[0][0] == "status"


@pytest.mark.parametrize(
    "action,expected", [("confirm", "confirm"), ("skip", "skip"), ("next", 1), ("previous", -1)]
)
def test_shortcuts_route_only_in_main_spectrum_workspace(action, expected):
    app, event, calls = shortcut_fixture()
    assert BatchWorkflowMixin._batch_shortcut(app, event, action) == "break"
    assert calls == [expected]
    app._spectrum_dialog_open = True
    assert BatchWorkflowMixin._batch_shortcut(app, event, action) is None
    assert calls == [expected]


def test_child_window_and_modal_grab_ignore_batch_shortcuts():
    app, event, calls = shortcut_fixture()
    event.widget.winfo_toplevel = lambda: object()
    assert BatchWorkflowMixin._batch_shortcut(app, event, "confirm") is None
    app, event, calls = shortcut_fixture()
    app.grab_current = lambda: object()
    assert BatchWorkflowMixin._batch_shortcut(app, event, "confirm") is None
    assert calls == []
