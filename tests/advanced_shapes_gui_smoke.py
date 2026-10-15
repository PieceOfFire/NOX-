"""Live group shape overrides, inherited defaults, formulas, undo and reopen."""

import tempfile
from pathlib import Path
from tkinter import filedialog, messagebox

import numpy as np

from gui_smoke import make_processed
from live_spin_gui_smoke import settled
from main import SpectrumApp
from spin_backend import data_snapshot, model_component_areas, same_state
from spintegrate.profiles import SHAPE_LABELS


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-shapes-") as directory:
            root = Path(directory)
            make_processed(root / "1")
            app.add_spectrum_folder(root / "1")
            original = data_snapshot(app.spectra[app.active_spectrum_id])
            app.open_spin_dialog()
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            w.parameters.vars["baseline.method"].set("none")
            w.parameters.vars["fit.threshold"].set("1500")
            settled(app, w)
            gid = w.group_specs[0]["id"]
            w.set_group_shape(gid, "asymmetric_pseudo_voigt")
            settled(app, w)
            state = w.states[w.active_sid]
            assert state["models"][0]["line_shapes"] == ["asymmetric_pseudo_voigt"] * 2
            assert state["groups"][0]["automatic"] and not state["groups"][0]["missing"]
            assert w.parameters.vars["fit.line_shape"].get() == SHAPE_LABELS["pseudo_voigt"]
            # A manual group inherits the general UI choice, not the automatic override.
            w.add_group(dict(id="manual", name="Manual"), [0])
            settled(app, w)
            assert w.states[w.active_sid]["models"][0]["line_shapes"] == [
                "pseudo_voigt",
                "asymmetric_pseudo_voigt",
            ]
            w.set_group_shape("manual", "phased_voigt")
            settled(app, w)
            model = w.states[w.active_sid]["models"][0]
            assert model["line_shapes"] == ["phased_voigt", "asymmetric_pseudo_voigt"]
            assert all(not g["missing"] for g in w.states[w.active_sid]["groups"])
            assert same_state(original, data_snapshot(app.spectra[app.active_spectrum_id]))
            w.groups.selection_set("manual")
            w.choose_group_shape()
            dialogs = [child for child in w.winfo_children() if child.winfo_class() == "Toplevel"]
            assert len(dialogs) == 1
            dialogs[0].attributes("-alpha", 0)
            dialogs[0].destroy()
            w.grab_set()
            areas = model_component_areas(model)
            w.apply()
            identifiers = [(i["id"], i["number"], i["spin_group_id"]) for i in app.integrals]
            before = [app.integral_area(i) for i in app.integrals]
            np.testing.assert_allclose(before, [areas[1], areas[0]])
            app.apply_calculation_preset("builtin:ratio")
            assert np.isclose(app.calculation_rows()[0][1][0][2].value, before[0] / before[1])
            # Reopening restores a general setting plus both explicit group choices.
            w = app.open_spin_diagnostics()
            settled(app, w)
            assert w.parameters.vars["fit.line_shape"].get() == SHAPE_LABELS["pseudo_voigt"]
            w.set_group_shape("manual", None)
            settled(app, w)
            assert w.states[w.active_sid]["models"][0]["line_shapes"][0] == "pseudo_voigt"
            w.apply()
            assert identifiers == [
                (i["id"], i["number"], i["spin_group_id"]) for i in app.integrals
            ]
            app.undo_last_action()
            state = app.spectra[app.active_spectrum_id]["spin_analyses"][
                app.spectra[app.active_spectrum_id]["last_spin_fit"]
            ]
            assert (
                next(g for g in state["groups"] if g["id"] == "manual")["line_shape"]
                == "phased_voigt"
            )
            np.testing.assert_allclose([app.integral_area(i) for i in app.integrals], before)
            app.redo_last_action()
            assert "line_shape" not in next(
                g
                for g in app.spectra[app.active_spectrum_id]["spin_analyses"][
                    app.spectra[app.active_spectrum_id]["last_spin_fit"]
                ]["groups"]
                if g["id"] == "manual"
            )
            path = root / "shapes.nmrproj.json"
            filedialog.asksaveasfilename = lambda **kw: str(path)
            app.save_project_as()
            assert path.is_file()
            filedialog.askopenfilename = lambda **kw: str(path)
            app.open_project()
            w = app.open_spin_diagnostics()
            settled(app, w)
            w.parameters.vars["fit.line_shape"].set(SHAPE_LABELS["voigt"])
            settled(app, w)
            assert w.states[w.active_sid]["models"][0]["line_shapes"] == [
                "voigt",
                "asymmetric_pseudo_voigt",
            ]
            w.destroy()
            assert not errors, errors
            print(
                "ADVANCED GUI OK: mixed profiles, inherited default, live refit, unchanged signal, formulas, identity, undo/redo, project reopen"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
