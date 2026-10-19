"""Selection-driven Spintegrate action, real NOX widgets and mixed integrals."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from tkinter import filedialog, messagebox
from main import SpectrumApp
from spin_backend import SPIN_DISPLAY, data_snapshot, same_state
from spin_dialog import SpinDialog
from spintegrate.core import lorentzian


def make_processed(folder, scale=1):
    folder.mkdir()
    pdata = folder / "pdata" / "1"
    pdata.mkdir(parents=True)
    x = np.linspace(4, 3, 4001)
    y = scale * (lorentzian(x, 3.5, 0.006, 12000) + lorentzian(x, 3.58, 0.009, 6500))
    y.astype("<f8").tofile(pdata / "1r")
    (pdata / "procs").write_text(
        "##$SI= 4001\n##$OFFSET= 4\n##$SW_p= 400.1\n##$SF= 400\n##$DTYPP= 2\n##$BYTORDP= 0\n",
        encoding="ascii",
    )


def wait(app):
    workspace = app._spin_workspace
    if workspace is None:
        app.update()
        return
    workspace.attributes("-alpha", 0)
    deadline = time.monotonic() + 60
    while not workspace.ready() or workspace.running or workspace.debounce:
        app.update()
        assert time.monotonic() < deadline, "Fit timed out"
        time.sleep(0.01)
    workspace.apply()
    app.update()


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-selection-") as folder:
            folder = Path(folder)
            make_processed(folder / "1")
            make_processed(folder / "2", 0.8)
            assert not hasattr(app, "work_mode")
            assert not hasattr(app, "mode_box")
            assert app.add_spectrum_folder(folder / "1")
            app.add_integral(3.4, 3.7)
            ordinary = app.integral_area(app.integrals[0])
            original = data_snapshot(app.spectra[app.active_spectrum_id])
            initial_items = copy.deepcopy(app.integrals)

            dialog = SpinDialog(app)
            dialog.attributes("-alpha", 0)
            assert not hasattr(dialog, "regions")
            assert not hasattr(dialog, "table")
            assert dialog.collect_recipe()["regions"] == []
            assert not any(key.startswith("processing.") for key in dialog.vars)
            dialog.vars["baseline.method"].set("none")
            dialog.vars["fit.threshold"].set("1500")
            dialog.run()
            assert app._spin_selecting and app.span.active
            assert app.spin_settings["regions"] == []
            assert app.integrals == initial_items  # parameters do not start a fit
            assert not app.spectra[app.active_spectrum_id].get("spin_analyses")
            app.on_x_selected(3.4, 3.7)
            wait(app)
            assert not errors, errors
            assert same_state(original, data_snapshot(app.spectra[app.active_spectrum_id]))
            assert len(app.integrals) == 2
            fitted = app.integrals[1]
            assert fitted["spin_fit_id"]
            area = app.integral_area(fitted)
            np.testing.assert_allclose(area, np.pi * 130.5, rtol=0.02)
            assert not np.isclose(area, ordinary, rtol=0.001)
            assert app.integral_area(app.integrals[0]) == ordinary
            app.undo_last_action()
            assert app.integrals == initial_items
            app.redo_last_action()
            assert app.integral_area(app.integrals[1]) == area
            app.apply_calculation_preset("builtin:ratio")
            assert np.isclose(app.calculation_rows()[0][1][0][2].value, ordinary / area)
            app.change_integration_display(SPIN_DISPLAY)
            assert app.integral_display_value(app.integrals[1]) == area / np.pi
            assert app.integral_display_value(app.integrals[0]) == ordinary
            assert np.isclose(app.calculation_rows()[0][1][0][2].value, ordinary / area)

            # Joint fit, then manually choose just one fitted component's interval.
            app.integral_table.selection_set(str(app.integrals[1]["id"]))
            app.arm_fitted_integral()
            count = len(app.spectra[app.active_spectrum_id]["spin_analyses"])
            app.on_x_selected(3.48, 3.52)
            assert len(app.integrals) == 3
            assert app.integrals[2]["spin_fit_id"] == app.integrals[1]["spin_fit_id"]
            assert len(app.spectra[app.active_spectrum_id]["spin_analyses"]) == count
            np.testing.assert_allclose(app.integral_area(app.integrals[2]), np.pi * 72, rtol=0.02)
            app.stop_spin_selection()
            assert not app._spin_selecting
            app.toggle_integration()
            assert app.integration_mode and not app._spin_selecting
            app.add_integral(3.48, 3.52)
            assert not app.integrals[-1].get(
                "spin_fit_id"
            )  # ∫ stays ordinary, even in a fitted area

            previous = app.capture_spectrum_state()
            app.phase0 = 5
            app.remember_spectrum_state(previous, "manual phase")
            app.draw()
            assert np.isnan(app.integral_area(app.integrals[1]))
            assert np.isfinite(app.integral_area(app.integrals[0]))
            assert app.calculation_rows()[0][1][0][2].status == "error"
            app.undo_last_action()
            assert app.integral_area(app.integrals[1]) == area
            app.remove_integral(app.integrals[1])
            app.undo_last_action()
            assert app.integral_area(app.integrals[1]) == area
            app.open_spin_diagnostics()
            for window in app.winfo_children():
                if window.winfo_class() == "Toplevel":
                    window.attributes("-alpha", 0)
                    window.destroy()
            calculation_status = app.formula_status.get()
            editor = app.open_formula_editor()
            editor.attributes("-alpha", 0)
            assert app.formula_status.get() == calculation_status
            editor.clean_formula_scene()
            editor.set_formula_tool("select")
            assert app.formula_status.get() == calculation_status
            editor.close()
            assert app.formula_status.get() == calculation_status

            payload = app.project_data()
            saved = folder / "project.nmrproj.json"
            saved.write_text(json.dumps(payload), encoding="utf-8")
            filedialog.askopenfilename = lambda **kw: str(saved)
            app.open_project()
            assert not errors, errors
            assert app.integral_area(app.integrals[1]) == area
            assert app.integral_area(app.integrals[0]) == ordinary
            assert app.spin_settings["regions"] == []

            assert app.add_spectrum_folder(folder / "2")
            ids = list(app.spectra)
            app.create_comparison(ids, "separate")
            app.add_comparison_integral(3.4, 3.7)
            before = app.capture_records(ids)
            app.arm_spin_selection(app.spin_settings)
            app.on_x_selected(3.4, 3.7)
            wait(app)
            assert not errors, errors
            items = app.comparison_record()["integrals"]
            assert len(items) == 2 and items[1]["spin_fit_id"]
            app.apply_calculation_preset("builtin:ratio")
            rows = [r[1][0][2].value for r in app.calculation_rows()]
            assert np.isfinite(rows).all()
            assert len(app.integral_table.get_children()) == 2
            # Undo the last preset action before undoing the fit action.
            app.undo_last_action()
            app.undo_last_action()
            assert same_state(before, app.capture_records(ids))
            app.redo_last_action()
            assert len(app.comparison_record()["integrals"]) == 2
            app.redo_last_action()
            assert rows == [r[1][0][2].value for r in app.calculation_rows()]
            after = app.capture_records(ids)
            app.on_x_selected(3.42, 3.68)
            app.cancel_spin_analysis()
            wait(app)
            assert same_state(after, app.capture_records(ids))
            assert not errors, errors
            print(
                "GUI OK: no modes/no proposed areas; parameters -> manual selection -> local fit; "
                "ordinary/fitted integrals independent; sub-integrals, formulas, undo/redo, "
                "diagnostics, save/load, comparison, cancel"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
