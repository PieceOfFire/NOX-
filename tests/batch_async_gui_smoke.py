"""Hidden Tk checks for paused/stale workers and replacement manual queues.

Only three generated spectra in a private temporary directory are used. Phases
are entered through the operator dialog; ordinary integrals need no model fit.
"""

import copy
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import batch_workflow
from gui_smoke import make_processed
from main import SpectrumApp
from nmr_dialogs import PhaseDialog
from processing_templates import validate_template
from spectrum_state import same_state


def pump(app, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        for child in app.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.attributes("-alpha", 0)
        app.update()
        assert time.monotonic() < deadline, (
            app.batch_heading.get(), app.batch_hint.get(), app.status.get()
        )
        time.sleep(0.01)
    app.update()


def poll_cycle(app):
    """Let the normal 250 ms queue poll run without starting another action."""
    ready = threading.Event()
    app.after(400, ready.set)
    pump(app, ready.is_set)


def manual_phase(app, value):
    app.open_phase_dialog()
    dialog = next(child for child in app.winfo_children() if isinstance(child, PhaseDialog))
    dialog.attributes("-alpha", 0)
    dialog.p0.set(value)
    dialog.p1.set(0)
    dialog.done()
    app.update()  # The normal after_idle callback starts target processing.


def raw_phase(record):
    return copy.deepcopy({
        key: record.get(key)
        for key in ("real", "imaginary", "phase0", "phase1", "phase_pivot", "phase_reference")
    })


class DelayedApply:
    def __init__(self, runner):
        self.runner = runner
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.calls = 0
        self.options = None

    def __call__(self, record, template, **options):
        self.calls += 1
        self.options = options
        self.started.set()
        try:
            assert self.release.wait(20), "Test did not release the controlled worker"
            return self.runner(record, template, **options)
        finally:
            self.finished.set()


def main():
    callbacks, dialogs, gates = [], [], []
    original_showerror = messagebox.showerror
    original_runner = batch_workflow.apply_template
    messagebox.showerror = lambda title, text, **kw: dialogs.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: callbacks.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-batch-async-") as directory:
            root = Path(directory)
            for name, scale in (("1", 1), ("2", 0.8), ("3", 0.6)):
                make_processed(root / name, scale)
                assert app.add_spectrum_folder(root / name)
            reference, second, third = ids = list(app.spectra)
            app.start_manual_batch(ids, reference_id=reference, formula={}, options={"align": False})
            session = app.batch_session
            manual_phase(app, 12)
            app.add_integral(3.45, 3.53)
            app.add_integral(3.54, 3.65)
            reference_before = copy.deepcopy(app.spectra[reference])
            app.batch_action()
            assert session.item(reference)["status"] == "confirmed"
            assert session.current_sid == second and not app._batch_running
            assert session.formula == {}
            assert validate_template(session.template)["formula"]["blocks"] == []

            # Pause while the detached worker waits. Retry invalidates its revision;
            # releasing it must not commit data or create an undo entry.
            paused = DelayedApply(original_runner)
            gates.append(paused)
            batch_workflow.apply_template = paused
            manual_phase(app, -7)
            pump(app, paused.started.is_set)
            in_flight = copy.deepcopy(app.spectra[second])
            original_input = raw_phase(in_flight)
            undo_count = len(app._undo_stack)
            revision = session.item(second)["revision"]
            assert paused.options == dict(align=False, phase_mode="preserve", allow_incomplete=True)
            app.batch_pause()
            assert session.paused
            assert session.item(second)["status"] == "awaiting_phase"
            assert session.item(second)["revision"] > revision
            paused.release.set()
            pump(app, lambda: paused.finished.is_set() and not app._batch_running)
            assert same_state(app.spectra[second], in_flight)
            assert len(app._undo_stack) == undo_count
            assert session.item(second)["status"] == "awaiting_phase"

            app.batch_pause()
            assert not session.paused
            poll_cycle(app)
            assert paused.calls == 1 and not app._batch_running
            assert same_state(app.spectra[second], in_flight)
            batch_workflow.apply_template = original_runner
            app.batch_action()  # An explicit command, not resume, reapplies the recipe.
            pump(app, lambda: not app._batch_running)
            assert session.item(second)["status"] == "review", session.item(second)
            assert not session.item(second)["blocking_issues"]
            assert session.item(second)["diagnostic"]["outputs"] == []
            assert len(app.spectra[second]["integrals"]) == 2
            assert same_state(raw_phase(app.spectra[second]), original_input)
            assert same_state(app.spectra[reference], reference_before)
            assert len(app._undo_stack) == undo_count + 1
            app.batch_action()
            assert session.current_sid == third and not app._batch_running

            # An in-place edit keeps the cheap token unchanged. The full worker
            # snapshot must still reject its stale result and preserve the edit.
            stale = DelayedApply(original_runner)
            gates.append(stale)
            batch_workflow.apply_template = stale
            manual_phase(app, 4)
            pump(app, stale.started.is_set)
            before_edit = copy.deepcopy(app.spectra[third])
            token = batch_workflow.record_token(app.spectra[third])
            app.spectra[third]["real"][0] += 0.25
            assert batch_workflow.record_token(app.spectra[third]) == token
            operator_change = copy.deepcopy(app.spectra[third])
            undo_count = len(app._undo_stack)
            stale.release.set()
            pump(app, lambda: stale.finished.is_set() and not app._batch_running)
            assert session.item(third)["status"] == "error", session.item(third)
            assert "Спектр изменился" in session.item(third)["error"]
            assert same_state(app.spectra[third], operator_change)
            assert not same_state(app.spectra[third], before_edit)
            assert len(app._undo_stack) == undo_count
            assert not app.spectra[third]["integrals"]
            assert same_state(app.spectra[reference], reference_before)

            # A new queue closes the old results view and clears its progress.
            # Capturing its sample advances once without processing the next item.
            old_window = app.show_batch_queue()
            old_window.attributes("-alpha", 0)
            app.update()
            assert len(old_window.table.get_children()) == 3
            old_session = session.to_payload()
            new_inputs = copy.deepcopy(app.spectra)
            batch_workflow.apply_template = original_runner
            app.start_manual_batch(
                [reference, third], reference_id=reference, formula={}, options={"align": False}
            )
            replacement = app.batch_session
            assert replacement is not session
            assert not old_window.winfo_exists() and app._batch_queue_window is None
            assert replacement.current_sid == reference
            assert replacement.current_item["status"] == "reference"
            assert replacement.template is None
            assert not app._batch_tokens and not app._batch_diagnostic_cache
            assert not app._batch_phase_templates
            assert same_state(app.spectra, new_inputs)
            app.batch_action()
            poll_cycle(app)
            assert replacement.current_sid == third
            assert replacement.item(reference)["status"] == "confirmed"
            assert replacement.item(third)["status"] == "awaiting_phase"
            assert not replacement.item(third)["phase_confirmed"]
            assert not app._batch_running
            assert same_state(app.spectra, new_inputs)
            assert session.to_payload() == old_session
            new_window = app.show_batch_queue()
            new_window.attributes("-alpha", 0)
            app.update()
            assert new_window is not old_window
            assert len(new_window.table.get_children()) == 2
            assert not callbacks, callbacks
            assert not dialogs, dialogs
            print(
                "Batch async GUI smoke passed: pause discards worker, resume requires action, "
                "raw/phase preserved, in-place edits protected, empty formula, replacement queue"
            )
    finally:
        for gate in gates:
            gate.release.set()
        batch_workflow.apply_template = original_runner
        messagebox.showerror = original_showerror
        app.destroy()


if __name__ == "__main__":
    main()
