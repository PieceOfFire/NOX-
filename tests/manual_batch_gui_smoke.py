"""Operator-led queue: actual dialogs, refits, corrections, resume and copying.

Only temporary synthetic Bruker folders are used. Run as a script, like the
other hidden desktop smoke checks; no private spectra or references are needed.
"""

import copy
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import batch_workflow
from gui_smoke import make_processed
from integral_editor_gui_smoke import drag
from main import SpectrumApp
from nmr_dialogs import CorrectionDialog, PhaseDialog
from processing_templates import apply_baseline_steps
from spectrum_state import same_state
from spin_backend import fit_current
from spin_dialog import SpinDialog


def hide_dialogs(app):
    for child in app.winfo_children():
        if isinstance(child, tk.Toplevel):
            child.attributes("-alpha", 0)


def pump(app, predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while not predicate():
        hide_dialogs(app)
        app.update()
        assert time.monotonic() < deadline, (
            app.batch_heading.get(), app.batch_hint.get(), app.status.get()
        )
        time.sleep(0.01)
    hide_dialogs(app)
    app.update()


def phase(app, phase0):
    """Exercise the normal operator command and its post-dialog callback."""
    app.open_phase_dialog()
    dialog = next(w for w in app.winfo_children() if isinstance(w, PhaseDialog))
    dialog.attributes("-alpha", 0)
    dialog.p1.set(0)
    dialog.p0.set(phase0)
    assert np.isclose(app.phase0, phase0)
    dialog.done()
    app.update()  # after_idle(on_manual_phase_committed) starts the worker


def processed(app, sid):
    pump(app, lambda: not app._batch_running and app.batch_session.item(sid)["status"] != "processing")
    item = app.batch_session.item(sid)
    assert item["status"] == "review", item
    assert not item["blocking_issues"], item


def main():
    callbacks, dialogs = [], []
    original_showerror = messagebox.showerror
    original_askyesno = messagebox.askyesno
    original_askopen = filedialog.askopenfilename
    original_runner = batch_workflow.apply_template
    messagebox.showerror = lambda title, text, **kw: dialogs.append((title, text))
    messagebox.askyesno = lambda *args, **kw: True
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.geometry("1250x850")
    app.report_callback_exception = lambda kind, value, tb: callbacks.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-manual-batch-") as directory:
            root = Path(directory)
            for name, scale in (("1", 1), ("2", .8), ("3", .7), ("4", .6), ("5", .5)):
                folder = root / name
                make_processed(folder, scale)
                path = folder / "pdata" / "1" / "1r"
                values = np.fromfile(path, dtype="<f8")
                values += scale * (250 + np.linspace(-30, 30, len(values)))
                values.tofile(path)
                assert app.add_spectrum_folder(folder)

            # Explicit non-project-index IDs catch saved queue mapping mistakes.
            originals = [copy.deepcopy(record) for record in app.spectra.values()]
            app.spectra.clear()
            app.active_spectrum_id = None
            ids = [10, 20, 30, 40, 50]
            for sid, record in zip(ids, originals):
                record["id"] = sid
                app.spectra[sid] = record
            app._next_spectrum_id = 51
            app.activate_spectrum(ids[0])
            app.apply_calculation_preset("builtin:ratio")
            formula = app.formula_preset_data()
            app.start_manual_batch(ids, reference_id=10, formula=formula, options={"align": False})
            session = app.batch_session
            assert session.current_item["status"] == "reference"
            assert not app._batch_running

            # The sample is processed using the same tools as an ordinary user.
            phase(app, 11)
            assert session.current_item["status"] == "reference" and not app._batch_running
            correction = CorrectionDialog(app)
            correction.attributes("-alpha", 0)
            correction.commit()
            assert len(app.spectra[10]["baseline_steps"]) == 1
            app.add_integral(3.4, 3.55)
            app.add_integral(3.56, 3.7)

            # Include a real fitted integral so IDs and component memberships
            # also survive replay and project remapping, not only rectangles.
            settings = SpinDialog(app)
            settings.attributes("-alpha", 0)
            settings.vars["baseline.method"].set("none")
            settings.vars["fit.threshold"].set("1500")
            settings.run()
            app.on_x_selected(3.4, 3.7)
            workspace = app._spin_workspace
            workspace.attributes("-alpha", 0)
            pump(app, lambda: not workspace.running and workspace.debounce is None and workspace.ready())
            workspace.apply()
            app.stop_spin_selection()
            app.update()
            assert len(app.integrals) == 3
            fit_id = app.integrals[-1]["spin_fit_id"]
            assert fit_current({**app.spectra[10], "spin": app.spectra[10]["spin_analyses"][fit_id]})
            reference_before = copy.deepcopy(app.spectra[10])
            app.batch_action()
            assert session.item(10)["status"] == "confirmed"
            assert session.current_sid == 20 and app.active_spectrum_id == 20
            assert not app._batch_running and not app.integrals
            original_template = session.template

            # Finishing manual phase is the trigger; sample phase is not copied.
            phase(app, -7)
            processed(app, 20)
            record = app.spectra[20]
            assert np.isclose(record["phase0"], -7)
            assert not np.isclose(record["phase0"], reference_before["phase0"])
            assert len(record["integrals"]) == 3
            assert record["baseline_steps"] == original_template["baseline_steps"]
            np.testing.assert_allclose(record["corrected"], apply_baseline_steps(record, record["baseline_steps"]))
            assert fit_current({**record, "spin": record["spin_analyses"][fit_id]})
            assert not same_state(record["corrected"], app.spectra[10]["corrected"])

            # A local limit correction survives confirmation and a second manual
            # phase pass; it does not implicitly alter the remaining recipe.
            app.toggle_integral_boundaries()
            drag(app, 3.4, 3.46)
            local_left = app.integrals[0]["left"]
            assert local_left > 3.45
            assert session.template == original_template
            phase(app, -8)
            processed(app, 20)
            assert np.isclose(app.phase0, -8)
            assert app.integrals[0]["left"] == local_left
            assert session.template == original_template

            # Deliberate update changes only the remaining recipe, not accepted
            # data or the reviewed result currently visible to the operator.
            before_update = copy.deepcopy(app.spectra[20])
            app.batch_update_template()
            assert session.template_revision == 2
            assert session.template["integrals"][0]["left"] == local_left
            assert same_state(app.spectra[10], reference_before)
            assert same_state(app.spectra[20], before_update)
            assert session.item(20)["status"] == "review"
            app.batch_action()
            assert session.item(20)["status"] == "confirmed"
            assert session.current_sid == 30

            # A failed calculation is shown as an error, never as zero-valued
            # integrals, and the operator can skip it and continue normally.
            def fail_third(record, template, **kwargs):
                if record["id"] == 30:
                    raise ValueError("Synthetic controlled processing failure")
                return original_runner(record, template, **kwargs)

            batch_workflow.apply_template = fail_third
            phase(app, 3)
            pump(app, lambda: not app._batch_running and session.item(30)["status"] == "error")
            assert "controlled processing failure" in session.item(30)["error"]
            assert not app.spectra[30]["integrals"]
            batch_workflow.apply_template = original_runner
            app.batch_skip()
            assert session.item(30)["status"] == "skipped"
            assert session.current_sid == 40

            # Save/reopen remaps original IDs to project indices. Loading always
            # pauses the queue and never starts applying any remaining template.
            app.batch_pause()
            assert session.paused
            payload = app.project_data()
            assert payload["manual_batch"]["reference_id"] == 0
            assert payload["manual_batch"]["current_id"] == 3
            saved = root / "paused-series.nmrproj.json"
            app.write_project(saved)
            filedialog.askopenfilename = lambda **kw: str(saved)
            app.open_project()
            app.update()
            restored = app.batch_session
            assert restored is not session
            assert restored.paused and not app._batch_running
            assert restored.reference_id == 1 and restored.current_sid == 4
            assert [item["status"] for item in restored.items] == [
                "confirmed", "confirmed", "skipped", "awaiting_phase", "awaiting_phase"
            ]
            assert not app.spectra[4]["integrals"]
            for sid in (1, 2):
                fitted = next(i for i in app.spectra[sid]["integrals"] if i.get("spin_fit_id"))
                assert fitted["spin_fit_id"] == fit_id
                assert fit_current({**app.spectra[sid], "spin": app.spectra[sid]["spin_analyses"][fit_id]})
            assert app.spectra[2]["integrals"][0]["left"] == local_left
            before_resume = copy.deepcopy(app.spectra[2])
            app.batch_pause()
            assert not restored.paused and not app._batch_running
            phase(app, 6)
            processed(app, 4)
            assert app.integrals[0]["left"] == local_left
            assert np.isclose(app.phase0, 6)
            app.batch_action()
            assert restored.current_sid == 5
            assert same_state(app.spectra[2], before_resume)
            app.batch_skip()
            assert restored.finished

            window = app.show_batch_queue()
            window.attributes("-alpha", 0)
            app.update()
            assert len(window.table.get_children()) == 5
            headers = [window.table.heading(c, "text") for c in window.table.cget("columns")]
            assert "I1" in headers and "I3" in headers
            first = window.table.item("0", "values")
            skipped = window.table.item("2", "values")
            assert first[1] == "Подтверждён" and all(v != "—" for v in first[3:])
            assert skipped[1] == "Пропущен" and all(v == "—" for v in skipped[3:])
            assert window.copy(SimpleNamespace(keycode=67, keysym="c")) == "break"
            copied = app.clipboard_get()
            assert "I1\tI2\tI3" in copied
            assert "Подтверждён" in copied and "Пропущен" in copied

            # Formula-free processing is a supported queue, not an invalid empty
            # formula preset. Pausing an in-flight worker must discard its result.
            app.start_manual_batch([1, 5], reference_id=1, formula={}, options={"align": False})
            app.batch_action()
            empty_formula_session = app.batch_session
            assert empty_formula_session.current_sid == 5
            assert empty_formula_session.formula == {}
            released = threading.Event()

            def slow_runner(record, template, **kwargs):
                assert released.wait(10), "Test did not release the paused worker"
                return original_runner(record, template, **kwargs)

            batch_workflow.apply_template = slow_runner
            phase(app, 2)
            assert app._batch_running
            in_flight_input = copy.deepcopy(app.spectra[5])
            app.batch_pause()
            assert empty_formula_session.paused
            released.set()
            pump(app, lambda: not app._batch_running)
            assert empty_formula_session.item(5)["status"] == "awaiting_phase"
            assert same_state(app.spectra[5], in_flight_input)
            batch_workflow.apply_template = original_runner
            app.batch_pause()
            assert not app._batch_running
            phase(app, 2)
            processed(app, 5)
            assert empty_formula_session.item(5)["diagnostic"]["outputs"] == []
            app.batch_action()
            assert empty_formula_session.finished
            assert not callbacks, callbacks
            assert not dialogs, dialogs
            print("Manual batch GUI smoke passed: phase, background, ordinary/model integrals, review, resume, Ctrl+C")
    finally:
        batch_workflow.apply_template = original_runner
        messagebox.showerror = original_showerror
        messagebox.askyesno = original_askyesno
        filedialog.askopenfilename = original_askopen
        app.destroy()


if __name__ == "__main__":
    main()
