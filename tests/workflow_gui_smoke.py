"""Real Tk: diagnostics, batch preview/apply/undo, live groups and report export."""

from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np

from gui_smoke import make_processed
from main import SpectrumApp
from nmr_dialogs import CorrectionDialog, PhaseDialog
from processing_templates import make_template, apply_baseline_steps
from spin_backend import same_state
from spin_dialog import SpinDialog


def wait(app, window):
    deadline = time.monotonic() + 60
    while window.poll_id is not None:
        app.update()
        assert time.monotonic() < deadline, window.status.get()
        time.sleep(0.01)
    app.update()


def settled(app, window):
    deadline = time.monotonic() + 60
    while window.debounce or window.running or not window.ready():
        app.update()
        assert time.monotonic() < deadline, window.status.get()
        time.sleep(0.01)
    app.update()


def button(parent, label):
    for child in parent.winfo_children():
        if isinstance(child, tk.Button) and child.cget("text") == label:
            return child
        found = button(child, label)
        if found:
            return found


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-workflow-") as directory:
            root = Path(directory)
            make_processed(root / "1")
            make_processed(root / "2", 0.8)
            app.add_spectrum_folder(root / "1")
            app.add_spectrum_folder(root / "2")
            ids = list(app.spectra)
            app.activate_spectrum(ids[0])
            app.add_integral(3.45, 3.53)
            app.add_integral(3.54, 3.65)
            app.apply_calculation_preset("builtin:ratio")
            dialog = CorrectionDialog(app)
            dialog.attributes("-alpha", 0)
            if dialog._job:
                dialog.after_cancel(dialog._job)
                dialog._job = None
            dialog.commit()
            r = app.spectra[ids[0]]
            assert len(r["baseline_steps"]) == 1
            np.testing.assert_allclose(app.corrected, apply_baseline_steps(r, r["baseline_steps"]))
            app.undo_last_action()
            assert not app.spectra[ids[0]].get("baseline_steps")
            app.redo_last_action()
            assert len(app.spectra[ids[0]]["baseline_steps"]) == 1
            phase = PhaseDialog(app)
            phase.attributes("-alpha", 0)
            phase.p0.set(0.2)
            phase.done()
            assert not app.spectra[ids[0]]["baseline_steps"]
            app.undo_last_action()
            assert len(app.spectra[ids[0]]["baseline_steps"]) == 1

            quality = app.open_integral_quality()
            quality.attributes("-alpha", 0)
            app.update()
            assert len(quality.table.get_children()) == 2
            assert quality.canvas.get_tk_widget().winfo_height() > 80
            quality.destroy()
            sensitivity = app.open_sensitivity()
            sensitivity.attributes("-alpha", 0)
            button(sensitivity, "Оценить").invoke()
            wait(app, sensitivity)
            assert len(sensitivity.table.get_children()) == 7
            assert app.last_sensitivity
            sensitivity.destroy()

            recipe = make_template(app.spectra[ids[0]], app.integrals, app.formula_preset_data())
            old = app.capture_workflow_state(ids)
            preview = app.preview_processing_template(
                recipe, old, dict(align=False, auto_phase=False), True
            )
            preview.attributes("-alpha", 0)
            wait(app, preview)
            assert same_state(old, app.capture_workflow_state(ids))
            assert len(preview.outputs) == 2
            button(preview, "Применить серию").invoke()
            app.update()
            assert not app._spectrum_dialog_open
            assert len(app.calculation_rows()) == 2
            assert all(len(app.spectra[s]["integrals"]) == 2 for s in ids)
            assert app.comparison_record()["mode"] == "separate"
            applied = app.capture_workflow_state(ids)
            app.undo_last_action()
            assert same_state(old, app.capture_workflow_state(ids))
            app.redo_last_action()
            assert same_state(applied, app.capture_workflow_state(ids))

            path = root / "report.html"
            filedialog.asksaveasfilename = lambda **kw: str(path)
            report = app.export_analysis_report()
            report.attributes("-alpha", 0)
            wait(app, report)
            assert path.exists()
            assert path.read_text(encoding="utf-8").count("<svg") == 2
            report.destroy()

            project = app.project_data()
            app.write_project(root / "project.json")
            filedialog.askopenfilename = lambda **kw: str(root / "project.json")
            app.open_project()
            assert len(app.spectra) == 2
            assert [r.get("baseline_steps") for r in app.spectra.values()] == [
                p["himera"]["baseline_steps"] for p in project["spectra"]
            ]

            app.current_comparison_id = None
            app.restore_active_spectrum(next(iter(app.spectra)))
            dialog = SpinDialog(app)
            dialog.attributes("-alpha", 0)
            dialog.vars["baseline.method"].set("none")
            dialog.vars["fit.threshold"].set("1500")
            dialog.run()
            app.on_x_selected(3.4, 3.7)
            spin = app._spin_workspace
            spin.attributes("-alpha", 0)
            settled(app, spin)
            spin.select_region.set(True)
            spin.select_component_region(3.49, 3.51)
            app.update()
            assert len(spin.selected_indices()) == 1
            spin.add_group(dict(id="manual", name="Chosen"), spin.selected_indices())
            spin.select_component_region(3.49, 3.51)
            app.update()
            spin.add_selection.set(True)
            spin.select_component_region(3.57, 3.59)
            app.update()
            assert len(spin.selected_indices()) == 2
            assert "RMSE/σ" in spin.notes.get("1.0", "end")
            spin.destroy()
            assert not errors, errors
            print(
                "WORKFLOW GUI OK: quality, baseline recipe/undo, sensitivity, atomic batch preview/undo/redo, multi-results, report, project, graphical group selection"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
