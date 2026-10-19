"""Diagnostic jumps never substitute another spectrum or another fit."""

import copy
import sys
from types import SimpleNamespace

import pytest

import himera_app
from himera_app import HimeraApp
from integral_inspector import IntegralInspector


def test_modal_diagnostic_blocks_global_mutating_shortcuts():
    app = SimpleNamespace(
        _spectrum_dialog_open=True, _spin_workspace=None, _spin_busy=False, _batch_running=False
    )
    assert HimeraApp.undo_last_action(app) == "break"
    assert HimeraApp.redo_last_action(app) == "break"
    assert HimeraApp.open_project(app) is None


def test_missing_explicit_comparison_does_not_fall_back_to_local_integral():
    changes, messages = [], []
    item = dict(id=1, number=1, left=2, right=3)
    app = SimpleNamespace(
        _spectrum_dialog_open=False,
        spectra={1: dict(integrals=[item])},
        active_spectrum_id=1,
        current_comparison_id=None,
        comparisons={},
        integrals=[item],
        status=SimpleNamespace(set=messages.append),
        activate_spectrum=lambda sid: changes.append(sid),
        integral_inspector=SimpleNamespace(select=lambda *a, **k: changes.append((a, k))),
    )
    assert not HimeraApp.focus_integral(app, 1, 1, comparison_id=100)
    assert not changes
    assert messages


def test_missing_integral_does_not_change_the_active_view():
    messages = []
    app = SimpleNamespace(
        _spectrum_dialog_open=False,
        spectra={1: {}, 2: dict(integrals=[])},
        active_spectrum_id=1,
        comparisons={},
        status=SimpleNamespace(set=messages.append),
    )
    assert not HimeraApp.focus_integral(app, 2, 1)
    assert messages


@pytest.mark.parametrize("method", ["edit_model", "table_double"])
def test_inspector_passes_the_selected_spectrum_to_the_fit_editor(method):
    calls = []
    inspector = SimpleNamespace(
        context=lambda: (None, dict(spin_fit_id="fit"), dict(id=2)),
        app=SimpleNamespace(open_spin_diagnostics=lambda *a, **k: calls.append((a, k))),
        table_click=lambda event: None,
        table_selected=lambda: None,
        zoom=lambda: None,
    )
    getattr(IntegralInspector, method)(inspector, *([None] if method == "table_double" else []))
    assert calls == [(("fit",), dict(spectrum_id=2))]


def test_plot_click_preserves_the_spectrum_of_the_model_curve():
    calls = []
    item = dict(id=1)
    line = SimpleNamespace(
        _integral_item_id=1, _integral_spectrum_id=2, contains=lambda event: (True, {})
    )
    axis = SimpleNamespace(lines=[line])
    app = SimpleNamespace(
        _spectrum_dialog_open=False,
        integration_mode=False,
        x_zoom_mode=False,
        _spin_selecting=False,
        integral_boundary_editor=SimpleNamespace(active=False),
        comparison_record=lambda: dict(integrals=[item]),
        _comparison_axes=[axis],
    )
    inspector = SimpleNamespace(app=app, select=lambda *a, **k: calls.append((a, k)))
    event = SimpleNamespace(button=1, xdata=3.5, inaxes=axis, dblclick=False)
    IntegralInspector.plot_click(inspector, event)
    assert calls == [((item,), dict(sid=2, zoom=False))]


@pytest.mark.parametrize("explicit_sid", [None, 2])
def test_comparison_fit_opens_on_selected_row_without_reordering_series(monkeypatch, explicit_sid):
    created = []

    def workspace(*args):
        created.append(args)
        return SimpleNamespace(parameters_changed=lambda: pytest.fail("Unexpected refit"))

    monkeypatch.setitem(sys.modules, "spin_workspace", SimpleNamespace(SpinWorkspace=workspace))
    monkeypatch.setattr(himera_app, "fit_current", lambda record: True)
    monkeypatch.setattr(himera_app, "data_snapshot", lambda record: dict(ppm_shift=0))
    records = {
        sid: dict(
            id=sid,
            ppm_shift=0,
            spin_analyses={
                "fit": dict(
                    models=[dict(low=sid, high=sid + 0.5)],
                    expected=dict(ppm_shift=0),
                    recipe={},
                )
            },
        )
        for sid in (1, 2)
    }
    before = copy.deepcopy(records)
    comparison = dict(id=10, spectrum_ids=[1, 2], integrals=[], offsets={2: 0.01})
    app = SimpleNamespace(
        _spin_busy=False,
        _spectrum_dialog_open=False,
        save_active_spectrum=lambda: None,
        integral_table=SimpleNamespace(selection=lambda: ("s2",)),
        comparison_record=lambda: comparison,
        active_spectrum_id=1,
        spectra=records,
        relevant_fit_ids=lambda record: {"fit"},
    )
    HimeraApp.open_spin_diagnostics(app, "fit", spectrum_id=explicit_sid)
    args = created[0]
    assert list(args[1]) == [1, 2]
    assert args[2]["initial_sid"] == 2
    assert args[2]["left"] == pytest.approx(2.01)
    assert args[2]["right"] == pytest.approx(2.51)
    assert records == before


def test_explicit_missing_fit_never_substitutes_another_fit(monkeypatch):
    messages = []
    monkeypatch.setattr(himera_app.messagebox, "showinfo", lambda *a, **k: messages.append(a))
    app = SimpleNamespace(
        _spin_busy=False,
        _spectrum_dialog_open=False,
        save_active_spectrum=lambda: None,
        integral_table=SimpleNamespace(selection=lambda: ()),
        comparison_record=lambda: None,
        active_spectrum_id=1,
        spectra={1: dict(id=1, spin_analyses={"another": {}})},
        relevant_fit_ids=lambda record: {"another"},
    )
    assert HimeraApp.open_spin_diagnostics(app, "missing") is None
    assert messages
