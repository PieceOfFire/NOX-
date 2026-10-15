"""Real Tk workflow: live refit -> groups -> formulas -> edit/undo/project."""

import json
from pathlib import Path
import tempfile
import time
import numpy as np
from tkinter import filedialog, messagebox
from gui_smoke import make_processed
from main import SpectrumApp
from spin_backend import data_snapshot, same_state
from spin_dialog import SpinDialog


def settled(app, workspace):
    workspace.attributes("-alpha", 0)
    deadline = time.monotonic() + 60
    while workspace.running or workspace.debounce or not workspace.ready():
        app.update()
        assert time.monotonic() < deadline, workspace.status.get()
        time.sleep(0.01)
    app.update()


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-live-") as directory:
            root = Path(directory)
            make_processed(root / "1")
            make_processed(root / "2", 0.8)
            app.add_spectrum_folder(root / "1")
            original = data_snapshot(app.spectra[app.active_spectrum_id])
            dialog = SpinDialog(app)
            dialog.attributes("-alpha", 0)
            dialog.vars["baseline.method"].set("none")
            dialog.vars["fit.threshold"].set("1500")
            dialog.run()
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            settled(app, w)
            assert not app.integrals  # preview is not an action
            assert w.canvas.get_tk_widget().winfo_height() > 180
            assert w.apply_button.winfo_ismapped()
            w.geometry("980x700")
            app.update()
            assert w.apply_button.winfo_ismapped()
            assert w.groups.winfo_ismapped()
            w.tables.select(w.lines_tab)
            app.update()
            assert w.components.winfo_ismapped()
            w.tables.select(w.output_tab)
            assert w.canvas.get_tk_widget().winfo_height() > 120
            w.geometry("1250x820")
            app.update()
            w.parameters.vars["fit.threshold"].set("не число")
            deadline = time.monotonic() + 5
            while w.debounce:
                app.update()
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert not w.ready() and str(w.apply_button.cget("state")) == "disabled"
            w.parameters.vars["fit.threshold"].set("1500")
            settled(app, w)
            w.add_group(dict(id="butene", name="Бутен"), [0])
            assert len(w.group_specs) == 2
            w.parameters.vars["fit.initial_width_hz"].set("6")
            w.parameters.vars["fit.initial_width_hz"].set("7")
            settled(app, w)
            assert w.settings["fit"]["initial_width_hz"] == 7
            assert w.states[w.active_sid]["groups"][1]["components"] == [0]
            assert same_state(original, data_snapshot(app.spectra[app.active_spectrum_id]))
            w.apply()
            assert len(app.integrals) == 2
            assert app.integrals[0]["left"] == app.integrals[1]["left"]
            areas = [app.integral_area(i) for i in app.integrals]
            np.testing.assert_allclose(areas, np.pi * np.array([58.5, 72]), rtol=0.02)
            identifiers = [(i["id"], i["number"], i["spin_group_id"]) for i in app.integrals]
            app.apply_calculation_preset("builtin:ratio")
            assert np.isclose(app.calculation_rows()[0][1][0][2].value, areas[0] / areas[1])
            before = app.capture_records([app.active_spectrum_id])
            w = app.open_spin_diagnostics()
            w.attributes("-alpha", 0)
            w.parameters.vars["fit.threshold"].set(
                "9000"
            )  # Whole-area remainder may legitimately become zero.
            settled(app, w)
            assert not any(g["missing"] for g in w.states[w.active_sid]["groups"])
            assert str(w.apply_button.cget("state")) == "normal"
            w.parameters.vars["fit.threshold"].set(
                "13000"
            )  # Now remove the manually assigned line.
            settled(app, w)
            assert any(g["missing"] for g in w.states[w.active_sid]["groups"])
            assert str(w.apply_button.cget("state")) == "disabled"
            assert same_state(before, app.capture_records([app.active_spectrum_id]))
            w.destroy()  # no edits committed
            assert same_state(before, app.capture_records([app.active_spectrum_id]))
            w = app.open_spin_diagnostics()
            w.attributes("-alpha", 0)
            w.parameters.vars["fit.initial_width_hz"].set("8")
            settled(app, w)
            w.apply()
            assert identifiers == [
                (i["id"], i["number"], i["spin_group_id"]) for i in app.integrals
            ]
            app.undo_last_action()
            assert same_state(before, app.capture_records([app.active_spectrum_id]))
            app.redo_last_action()
            saved = root / "project.nmrproj.json"
            saved.write_text(json.dumps(app.project_data()), encoding="utf-8")
            filedialog.askopenfilename = lambda **kw: str(saved)
            app.open_project()
            assert identifiers == [
                (i["id"], i["number"], i["spin_group_id"]) for i in app.integrals
            ]
            np.testing.assert_allclose(
                [app.integral_area(i) for i in app.integrals], areas, rtol=0.02
            )
            app.add_spectrum_folder(root / "2")
            app.create_comparison(list(app.spectra), "separate")
            app.arm_spin_selection(app.spin_settings)
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            settled(app, w)
            w.add_group(dict(id="group-a", name="Группа A"), [0])
            w.apply()
            app.apply_calculation_preset("builtin:ratio")
            rows = [r[1][0][2].value for r in app.calculation_rows()]
            np.testing.assert_allclose(rows, [58.5 / 72] * 2, rtol=0.02)
            assert len(app.comparison_record()["integrals"]) == 2
            saved.write_text(json.dumps(app.project_data()), encoding="utf-8")
            app.open_project()
            np.testing.assert_allclose([r[1][0][2].value for r in app.calculation_rows()], rows)
            app.spin_settings["fit"]["threshold"] = 1e9
            app.arm_spin_selection(app.spin_settings)
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            settled(app, w)
            assert not len(w.states[w.active_sid]["models"][0]["centers"])
            assert "Линий не найдено" in w.status.get()
            w.destroy()
            assert not errors, errors
            print(
                "LIVE GUI OK: preview isolation, coalesced refit, multiple groups/domain, missing-line guard, edit/undo, save/load, comparison formulas"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
