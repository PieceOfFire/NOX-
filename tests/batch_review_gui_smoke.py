"""Queue review cannot accept undone processing or an unconfirmed phase."""

import copy
from pathlib import Path
import tempfile
from tkinter import messagebox

from gui_smoke import make_processed
from main import SpectrumApp
from manual_batch_gui_smoke import phase, processed, pump
from nmr_dialogs import CorrectionDialog
from spectrum_state import same_state


def edit_phase_in_history(app, value):
    """The same state change that phase undo/redo exposes outside a dialog."""
    previous = app.capture_spectrum_state()
    app.phase0 = value
    app.corrected = None
    app.spectra[app.active_spectrum_id]["baseline_steps"] = []
    app.remember_spectrum_state(previous, "ручное изменение фазы для проверки истории")
    app.draw()


def main():
    callbacks, dialogs = [], []
    original_error = messagebox.showerror
    messagebox.showerror = lambda title, text, **kw: dialogs.append((title, str(text)))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.geometry("1200x820")
    app.report_callback_exception = lambda kind, value, tb: callbacks.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-review-history-") as directory:
            root = Path(directory)
            for name, scale in (("1", 1), ("2", .8), ("3", .6)):
                make_processed(root / name, scale)
                assert app.add_spectrum_folder(root / name)
            app.start_manual_batch([1, 2, 3], reference_id=1, formula={}, options={"align": False})
            correction = CorrectionDialog(app)
            correction.attributes("-alpha", 0)
            correction.commit()
            app.add_integral(3.4, 3.55)
            app.add_integral(3.56, 3.7)
            app.batch_action()
            phase(app, 5)
            processed(app, 2)
            session = app.batch_session
            ready = copy.deepcopy(app.spectra[2])

            # Undoing the worker restores an empty/unprocessed target. Even
            # without a formula, immediate confirm cannot approve/advance it.
            app.undo_last_action()
            assert not app.integrals and app.corrected is None
            app.batch_action()
            assert session.current_sid == 2
            assert session.current_item["status"] != "confirmed"
            assert session.current_item["blocking_issues"]
            assert dialogs and "интеграл" in dialogs[-1][1].lower()
            app.redo_last_action()
            assert same_state(app.spectra[2], ready)
            diagnostic = app._update_batch_review(2)
            assert not diagnostic["blocking_issues"]
            assert session.current_item["status"] == "review"

            # Without a formula, losing just one expected integral must still
            # remain a missing value through repeated checks, never be forgotten
            # because the previous diagnostic was replaced by a shorter result.
            app.remove_integral(app.integrals[1])
            missing = app._update_batch_review(2)
            assert missing["blocking_issues"] and missing["integrals"]["2"] is None
            again = app._update_batch_review(2)
            assert again["blocking_issues"] and again["integrals"]["2"] is None
            app.batch_action()
            assert session.current_sid == 2 and session.current_item["status"] == "review"
            app.undo_last_action()
            assert same_state(app.spectra[2], ready)
            restored = app._update_batch_review(2)
            assert not restored["blocking_issues"] and restored["integrals"]["2"] is not None

            # No 250ms poll is allowed to be required for safety: pressing the
            # old confirmation button immediately after an undo/phase edit must
            # invalidate phase and leave the operator on the same queue item.
            edit_phase_in_history(app, 12)
            app.batch_action()
            assert session.current_sid == 2
            assert session.current_item["status"] == "awaiting_phase"
            assert not session.current_item["phase_confirmed"]
            assert not app._batch_running
            app.undo_last_action()
            assert app.phase0 == 5
            assert session.current_item["status"] == "awaiting_phase"
            assert not app._batch_running
            app.redo_last_action()
            assert app.phase0 == 12
            app.batch_phase_ready()  # an explicit operator phase-ready action
            processed(app, 2)
            assert app.phase0 == 12 and len(app.integrals) == 2
            assert session.current_item["phase_signature"] == [12, 0, .5]

            # Already confirmed rows also need a synchronous guard. Revisiting
            # one and changing phase must not bypass review through "Next".
            app.batch_action()
            assert session.item(2)["status"] == "confirmed"
            assert session.current_sid == 3
            # A multi-record workflow transaction can restore an earlier target
            # while another spectrum is selected. Its old accepted badge must
            # be invalidated too, not only the currently visible queue item.
            app._undo_stack.append((
                "himera_records",
                "проверка отмены обработки предыдущего спектра",
                {"records": {2: copy.deepcopy(ready)}, "comparisons": copy.deepcopy(app.comparisons)},
            ))
            app.undo_last_action()
            assert app.active_spectrum_id == 3 and session.current_sid == 3
            pump(app, lambda: session.item(2)["status"] == "awaiting_phase")
            assert not session.item(2)["phase_confirmed"]
            assert app.active_spectrum_id == 3 and session.current_sid == 3
            app.redo_last_action()
            assert app.spectra[2]["phase0"] == 12
            assert session.item(2)["status"] == "awaiting_phase"
            app.batch_open_item(2)
            app.batch_phase_ready()
            processed(app, 2)
            app.batch_action()
            assert session.item(2)["status"] == "confirmed" and session.current_sid == 3
            app.batch_open_item(2)
            edit_phase_in_history(app, 18)
            app.batch_action()
            assert session.current_sid == 2
            assert session.current_item["status"] == "awaiting_phase"
            assert not session.current_item["phase_confirmed"]
            assert not app._batch_running
            assert not callbacks, callbacks
            print("Batch review/history GUI smoke passed: empty/partial undo, redo, phase guard, confirmed-row guard")
    finally:
        messagebox.showerror = original_error
        app.destroy()


if __name__ == "__main__":
    main()
