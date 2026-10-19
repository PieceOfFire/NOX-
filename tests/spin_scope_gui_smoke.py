"""Visible operation scope, safe shortcuts, cancellable progress and phase input."""

import copy
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
from tkinter import messagebox, simpledialog

import numpy as np

from gui_smoke import make_processed
from live_spin_gui_smoke import settled
from main import SpectrumApp
from nmr_dialogs import PhaseDialog
from spin_backend import same_state
import spin_workspace


def pump(app, predicate, timeout=20):
    until = time.monotonic() + timeout
    while not predicate():
        app.update()
        assert time.monotonic() < until, "GUI operation timed out"
        time.sleep(0.01)
    app.update()


def phase_checks(app):
    app.corrected = app.real.copy() - 10
    saved = app.capture_spectrum_state()
    phase = PhaseDialog(app)
    phase.attributes("-alpha", 0)
    phase.done()
    assert same_state(saved, app.capture_spectrum_state()), "Unchanged phase must retain baseline"

    phase = PhaseDialog(app)
    phase.attributes("-alpha", 0)
    valid_phase = (app.phase0, app.phase1)
    for invalid in ("", "bad", "NaN", "inf", "-Infinity"):
        phase.p0.set(invalid)
        app.update()
        assert str(phase.done_button.cget("state")) == "disabled"
        assert phase.validation_error.get().startswith("φ0:")
        assert phase.phase_entries[0][0].cget("background") == "#fff1f2"
        phase.done()  # direct callback is guarded as well as the disabled button
        phase.start_drag(SimpleNamespace(y=10, state=0))
        assert phase.winfo_exists() and phase.drag_start_y is None
        assert valid_phase == (app.phase0, app.phase1)
        np.testing.assert_array_equal(app.corrected, saved["corrected"])
    phase.p0.set("0,25")
    assert not phase.validation_error.get()
    phase.p1.set("NaN")
    assert "φ1:" in phase.validation_error.get()
    phase.p1.set("0")
    assert str(phase.done_button.cget("state")) == "normal"
    assert np.isclose(app.phase0, 0.25)
    phase.done()
    assert not app._spectrum_dialog_open and app.corrected is None
    phase = PhaseDialog(app)
    phase.attributes("-alpha", 0)
    phase.p1.set("bad")
    phase.cancel()
    assert np.isclose(app.phase0, 0.25) and np.isfinite(app.phase1)


def main():
    errors = []
    messagebox.showerror = lambda title, text, **_kw: errors.append((title, text))
    original_runner = spin_workspace.run_selected_region
    original_ask = simpledialog.askstring
    release = threading.Event()
    old_preferences = os.environ.get("HIMERA_VIEW_PREFERENCES")
    with tempfile.TemporaryDirectory(prefix="himera-scopes-") as directory:
        root = Path(directory)
        os.environ["HIMERA_VIEW_PREFERENCES"] = str(root / "view.json")
        app = SpectrumApp()
        app.attributes("-alpha", 0)
        app.report_callback_exception = lambda kind, value, _tb: errors.append(
            (str(kind), str(value))
        )
        try:
            for name, scale in (("1", 1), ("2", 0.8)):
                make_processed(root / name, scale)
                assert app.add_spectrum_folder(root / name)
            phase_checks(app)
            app.create_comparison(list(app.spectra), "separate")
            app.open_spin_dialog()
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            w.attributes("-alpha", 0)
            w.parameters.vars["baseline.method"].set("none")
            w.parameters.vars["fit.threshold"].set("1500")
            settled(app, w)
            ids = list(w.records)
            first, second = ids
            assert w.series_scope() == "Вся серия · 2"
            assert w.current_scope.get() == "Только текущий · 1"
            assert str(w.progress.cget("mode")) == "determinate"
            w.components.selection_set("0")
            w.group_name.set("Общий")
            w.group_name_entry.focus_force()
            app.update()
            w.group_name_entry.event_generate("<Return>")
            app.update()
            assert len(w.group_specs) == 1 and w.group_specs[0]["name"] == "Общий"
            gid = w.group_specs[0]["id"]
            assert all(state["groups"][0]["id"] == gid for state in w.states.values())
            original_members = w.states[first]["groups"][0]["components"].copy()
            assert original_members

            assert w.step_spectrum(1, SimpleNamespace(widget=w.group_name_entry)) is None
            assert w.active_sid == first
            w.groups.focus_force()
            app.update()
            assert w.groups.winfo_height() >= 70, "Scope labels must not hide the integral rows"
            w.groups.event_generate("<Control-Next>")
            app.update()
            assert w.active_sid == second and w.current_scope.get().endswith("2"), (
                w.active_sid,
                first,
                second,
                w.current_scope.get(),
                w.focus_get(),
                w.groups.bindtags(),
                w.bind("<Control-Next>"),
            )
            w.groups.selection_set(gid)
            w.components.selection_set([])
            w.assign_selected()
            assert w.states[second]["groups"][0]["components"] == []
            assert w.states[first]["groups"][0]["components"] == original_members

            prompts = []

            def rename(_title, prompt, **_kwargs):
                prompts.append(prompt)
                return "Переименован"

            simpledialog.askstring = rename
            w.rename_group_key(SimpleNamespace(widget=w.group_name_entry))
            assert not prompts
            w.groups.focus_force()
            app.update()
            w.groups.event_generate("<F2>")
            app.update()
            assert len(prompts) == 1 and "вся серия · 2" in prompts[0]
            assert all(s["groups"][0]["name"] == "Переименован" for s in w.states.values())
            w.delete_group()
            assert all(not s["groups"] for s in w.states.values())
            w.undo_edit()
            assert all(s["groups"][0]["id"] == gid for s in w.states.values())

            saved = copy.deepcopy(w.states)
            started, calls = threading.Event(), []

            def delayed(*args, **kwargs):
                calls.append(args[0]["name"])
                started.set()
                assert release.wait(15)
                return original_runner(*args, **kwargs)

            spin_workspace.run_selected_region = delayed
            w.parameters.vars["fit.initial_width_hz"].set("9")
            pump(app, lambda: started.is_set() and "1/2" in w.progress_info.get())
            assert str(w.stop_button.cget("state")) == "normal"
            w.stop_fit()
            assert w.ready() and same_state(saved, w.states)
            release.set()
            pump(app, lambda: not w.running)
            assert calls == ["1"], "Cancelled revision must not start the next spectrum"
            assert same_state(saved, w.states)
            assert str(w.stop_button.cget("state")) == "disabled"

            release.clear()
            started.clear()
            calls.clear()

            def delayed_second(*args, **kwargs):
                calls.append(args[0]["name"])
                if len(calls) == 2:
                    started.set()
                    assert release.wait(15)
                return original_runner(*args, **kwargs)

            spin_workspace.run_selected_region = delayed_second
            w.parameters.vars["fit.initial_width_hz"].set("8")
            pump(app, lambda: started.is_set() and "2/2" in w.progress_info.get())
            assert float(w.progress.cget("value")) == 1
            release.set()
            settled(app, w)
            assert float(w.progress.cget("value")) == 2
            w.geometry("980x700")
            app.update()
            for control in (w.create_button, w.replace_button, w.apply_button, w.stop_button):
                assert control.winfo_ismapped()
                assert (
                    control.winfo_rootx() + control.winfo_width()
                    <= w.winfo_rootx() + w.winfo_width()
                )
            assert w.canvas.get_tk_widget().winfo_height() > 120
            assert not errors, errors
            print(
                "SPIN SCOPE GUI OK: operation scope, Enter/F2/spectrum keys, progress/stop, finite phase"
            )
        finally:
            release.set()
            spin_workspace.run_selected_region = original_runner
            simpledialog.askstring = original_ask
            app.destroy()
            if old_preferences is None:
                os.environ.pop("HIMERA_VIEW_PREFERENCES", None)
            else:
                os.environ["HIMERA_VIEW_PREFERENCES"] = old_preferences


if __name__ == "__main__":
    main()
