"""A damaged spectrum retains earlier checks and never latches disabled controls."""

import copy
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui_smoke import make_processed
from main import SpectrumApp
from sensitivity import analyze_sensitivity
from spectrum_state import same_state
from test_quantitative_workflow import inputs
from workflow_tools import ResultsWindow


def pump(app, predicate):
    end = time.monotonic() + 15
    while not predicate():
        app.update()
        assert time.monotonic() < end
        time.sleep(0.01)
    app.update()


def main():
    with tempfile.TemporaryDirectory(prefix="himera-sensitivity-error-") as directory:
        directory = Path(directory)
        os.environ["HIMERA_VIEW_PREFERENCES"] = str(directory / "view.json")
        app = SpectrumApp()
        app.attributes("-alpha", 0)
        callbacks = []
        app.report_callback_exception = lambda kind, value, tb: callbacks.append(
            (str(kind), str(value))
        )
        try:
            for n in range(1, 4):
                make_processed(directory / str(n), n)
                assert app.add_spectrum_folder(directory / str(n))
            ids = list(app.spectra)
            app.create_comparison(ids, "common")
            app.add_comparison_integral(3.45, 3.53)
            app.add_comparison_integral(3.54, 3.65)
            _, app.formula_document = inputs()
            before = copy.deepcopy(app.capture_records(ids))
            window = app.open_sensitivity()
            window.attributes("-alpha", 0)
            run = next(
                w
                for w in window.options.winfo_children()
                if isinstance(w, tk.Button) and w.cget("text") == "Оценить"
            )

            def one_failure(record, *args, **options):
                if record["id"] == ids[1]:
                    raise ValueError("Damaged synthetic spectrum")
                return analyze_sensitivity(record, *args, **options)

            with patch("workflow_tools.analyze_sensitivity", side_effect=one_failure):
                run.invoke()
                pump(app, lambda: not window.running)
            rows = [window.table.item(iid, "values") for iid in window.table.get_children()]
            assert len(rows) == 11  # first five, one unavailable, last five
            assert all(row[0] == "1" for row in rows[:5])
            assert rows[5][0] == "2" and rows[5][3] == "—"
            assert "Damaged" in rows[5][4]
            assert all(row[0] == "3" for row in rows[6:])
            assert "10/15" in window.status.get() and "неполная" in window.status.get()
            assert app.last_sensitivity is None
            assert run.cget("state") == "normal"
            assert same_state(before, app.capture_records(ids))

            run.invoke()
            pump(app, lambda: not window.running)
            assert len(window.table.get_children()) == 15
            assert len(app.last_sensitivity) == 15

            with patch(
                "workflow_tools.sensitivity_case_count", side_effect=RuntimeError("Planning failed")
            ):
                run.invoke()  # fail before the per-record try block
                pump(app, lambda: not window.running)
            assert "Planning failed" in window.status.get()
            assert run.cget("state") == "normal"
            assert len(window.table.get_children()) == 15
            assert app.last_sensitivity is None
            run.invoke()
            pump(app, lambda: not window.running)
            assert len(app.last_sensitivity) == 15
            window.destroy()

            # The error hook itself belongs to the GUI thread, not the worker.
            generic = ResultsWindow(app, "test", ("A",))
            generic.attributes("-alpha", 0)
            gui_thread = threading.get_ident()
            failure_threads = []

            def fail(_stop):
                assert threading.get_ident() != gui_thread
                raise RuntimeError("Controlled worker failure")

            generic.start(
                fail,
                lambda _rows: callbacks.append("unexpected done"),
                failed=lambda _error: failure_threads.append(threading.get_ident()),
            )
            pump(app, lambda: not generic.running)
            assert failure_threads == [gui_thread]
            assert "Controlled worker failure" in generic.status.get()
            assert str(generic.stop_button.cget("state")) == "disabled"
            generic.destroy()
            assert not callbacks, callbacks
        finally:
            app.destroy()
    print("sensitivity_failure_gui_smoke: PASS")


if __name__ == "__main__":
    main()
