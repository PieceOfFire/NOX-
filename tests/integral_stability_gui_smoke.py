"""Formula-free checks, selected integrals, comparison offsets and detached data."""

import copy
from pathlib import Path
import sys
import tempfile
import threading
import time
from tkinter import messagebox
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui_smoke import make_processed
from main import SpectrumApp
from spin_backend import default_recipe, run_selected_region, same_state


def wait(app, window):
    deadline = time.monotonic() + 50
    while window.poll_id is not None:
        app.update()
        assert time.monotonic() < deadline, window.status.get()
        time.sleep(0.01)
    app.update()


def main():
    errors = []
    original = messagebox.showinfo
    messagebox.showinfo = lambda *args, **kwargs: errors.append(args)
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-stability-") as directory:
            root = Path(directory)
            for name, scale in (("1", 1), ("2", 0.8)):
                make_processed(root / name, scale)
                assert app.add_spectrum_folder(root / name)
            ids = list(app.spectra)
            app.activate_spectrum(ids[0])
            app.add_integral(3.45, 3.53)
            app.add_integral(3.54, 3.65)
            assert not app.formula_document.blocks
            app.integral_table.selection_set(str(app.integrals[0]["id"]))
            before = app.capture_records(ids)
            history = copy.deepcopy(app._undo_stack)
            window = app.open_integral_stability()
            window.attributes("-alpha", 0)
            window.stability_run()
            wait(app, window)
            assert len(window.summaries) == 1
            assert len(window.rows) == 13
            assert len(window.table.get_children()) == 1
            assert len(window.axes.patches) == 3
            assert window.canvas.get_tk_widget().winfo_height() > 80
            window.stability_toggle_details()
            app.update()
            assert len(window.table.get_children()) == 13
            window.table.selection_set("0", "1")
            assert app.copy_treeview_rows(window.table, context="stability")
            copied = app.clipboard_get()
            assert "Сценарий" in copied and "Исходный" in copied
            assert same_state(before, app.capture_records(ids))
            assert same_state(history, app._undo_stack)
            window.destroy()
            assert not app._spectrum_dialog_open

            # Both stacked and common comparisons use the same contexts and
            # compensate display offsets rather than moving the sampled data.
            for mode in ("common", "separate"):
                app.create_comparison(ids, mode)
                app.add_comparison_integral(3.45, 3.53)
                comparison = app.comparison_record()
                comparison["offsets"][ids[1]] = 0.002
                app.draw_comparison()
                app.integral_table.selection_remove(app.integral_table.selection())
                before_comparison = copy.deepcopy(comparison)
                before_analysis = app.capture_records(ids)
                window = app.open_integral_stability()
                window.attributes("-alpha", 0)
                window.stability_run()
                wait(app, window)
                assert len(window.summaries) == 2
                assert {s["spectrum_id"] for s in window.summaries} == set(ids)
                assert same_state(before_comparison, app.comparison_record())
                assert same_state(before_analysis, app.capture_records(ids))
                assert same_state(before["records"], app.capture_records(ids)["records"])
                window.destroy()

            # Model groups are full areas, not numerical integrals of the
            # highlighted rectangle, and the UI must retain model identities.
            app.activate_spectrum(ids[0])
            record = app.spectra[ids[0]]
            settings = default_recipe()
            settings["baseline_policy"] = "manual"
            settings["baseline_default"]["method"] = "none"
            settings["fit"]["threshold"] = 1500
            state = run_selected_region(record, settings, 3.4, 3.7)
            assert len(state["models"][0]["centers"]) >= 2
            state["groups"] = [
                dict(id="a", name="A", components=[0], missing=0),
                dict(id="b", name="B", components=[1], missing=0),
            ]
            record["spin_analyses"] = {"test-fit": state}
            for i, gid in enumerate(("a", "b"), 3):
                app.integrals.append(
                    dict(
                        id=i,
                        number=i,
                        name=f"Model {gid}",
                        color="#4c78a8",
                        left=3.4,
                        right=3.7,
                        spin_fit_id="test-fit",
                        spin_group_id=gid,
                    )
                )
            app.draw()
            app.integral_table.selection_set("3", "4")
            before_model = app.capture_records(ids)
            window = app.open_integral_stability()
            window.attributes("-alpha", 0)
            window.stability_run()
            wait(app, window)
            assert len(window.summaries) == 2
            assert all(s["method"] == "Spintegrate" for s in window.summaries)
            assert len(window.rows) == 26
            assert same_state(before_model, app.capture_records(ids))
            window.destroy()

            with patch(
                "integral_stability_view.analyze_integral_stability",
                side_effect=ValueError("Invalid synthetic data"),
            ):
                window = app.open_integral_stability()
                window.attributes("-alpha", 0)
                window.stability_run()
                wait(app, window)
                assert len(window.summaries) == 2
                assert all(s["nominal"] is None and s["failed"] for s in window.summaries)
                assert all(r["value"] is None for r in window.rows)
                window.destroy()

            entered, finished = threading.Event(), threading.Event()

            def delayed(*args, stop, **kwargs):
                entered.set()
                assert stop.wait(4)
                finished.set()
                return []

            with patch("integral_stability_view.analyze_integral_stability", side_effect=delayed):
                window = app.open_integral_stability()
                window.attributes("-alpha", 0)
                window.stability_run()
                assert entered.wait(2)
                window.destroy()
                assert finished.wait(2)
                app.update()
            assert same_state(before_model, app.capture_records(ids))
            assert not errors, errors
            print(
                "Integral stability GUI passed: no formula, selection, summary/details/plot, Ctrl+C, comparison offsets, no mutation"
            )
    finally:
        messagebox.showinfo = original
        app.destroy()


if __name__ == "__main__":
    main()
