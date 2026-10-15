"""Refit dynamic whole-area outputs without losing lines or changing fit metrics."""

from pathlib import Path
import tempfile
from tkinter import messagebox

import numpy as np

from gui_smoke import make_processed
from live_spin_gui_smoke import settled
from main import SpectrumApp
from signal_quality import fit_quality
from spin_backend import data_snapshot, same_state, model_component_areas


def assert_whole(workspace, count):
    state = workspace.states[workspace.active_sid]
    model = state["models"][0]
    group = state["groups"][0]
    assert len(model["centers"]) == count
    assert group["automatic"] and group["missing"] == 0
    assert group["components"] == list(range(count))
    assert len(workspace.component_artists) == count
    assert not workspace.group_artists  # Full model already represents whole-area sum.
    np.testing.assert_allclose(
        sum(
            (artist.get_ydata() for artist in workspace.component_artists),
            np.zeros(len(model["x"])),
        ),
        model["fitted"],
        atol=1e-6,
    )
    assert str(workspace.apply_button.cget("state")) == "normal"
    assert "потеряны" not in str(workspace.groups.item(group["id"], "values"))


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-whole-") as directory:
            folder = Path(directory) / "Demo"
            make_processed(folder)
            app.add_spectrum_folder(folder)
            before = data_snapshot(app.spectra[app.active_spectrum_id])
            app.open_spin_dialog()
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            w.parameters.vars["baseline.method"].set("none")
            w.parameters.vars["fit.threshold"].set("1500")
            settled(app, w)
            assert_whole(w, 2)
            w.parameters.vars["fit.threshold"].set("11000")
            settled(app, w)
            assert_whole(w, 1)
            w.parameters.auto_button.invoke()
            settled(app, w)
            assert_whole(w, 2)
            state = w.states[w.active_sid]
            metric = fit_quality(state["models"][0])["r_squared"]
            fitted = state["models"][0]["fitted"].copy()
            settings = w.parameters.collect_recipe()
            w.parameters.auto_button.invoke()
            settled(app, w)
            assert_whole(w, 2)
            assert same_state(settings, w.parameters.collect_recipe())
            np.testing.assert_array_equal(fitted, w.states[w.active_sid]["models"][0]["fitted"])
            assert fit_quality(w.states[w.active_sid]["models"][0])["r_squared"] == metric
            area = sum(model_component_areas(w.states[w.active_sid]["models"][0]))
            w.apply()
            assert np.isclose(app.integral_area(app.integrals[0]), area)
            assert same_state(before, data_snapshot(app.spectra[app.active_spectrum_id]))
            w = app.open_spin_diagnostics()
            settled(app, w)
            assert w._whole_area_group_id == w.group_specs[0]["id"]
            w.parameters.vars["fit.threshold"].set("11000")
            settled(app, w)
            assert_whole(w, 1)
            w.parameters.vars["fit.threshold"].set("1500")
            settled(app, w)
            assert_whole(w, 2)
            # An explicit manual assignment must no longer auto-expand on refit.
            fitted = w.states[w.active_sid]["models"][0]["fitted"].copy()
            w.components.selection_set("1")
            w.assign_selected()
            assert w._whole_area_group_id is None
            assert "automatic" not in w.states[w.active_sid]["groups"][0]
            assert fit_quality(w.states[w.active_sid]["models"][0])["r_squared"] == metric
            np.testing.assert_array_equal(fitted, w.states[w.active_sid]["models"][0]["fitted"])
            w.parameters.vars["fit.threshold"].set("11000")
            settled(app, w)
            assert w.states[w.active_sid]["groups"][0]["missing"] == 1
            assert str(w.apply_button.cget("state")) == "disabled"
            w.destroy()
            assert not errors, errors
            print(
                "WHOLE GUI OK: 2→1→2 refit, all curves accounted for, repeatable auto/R², saved edit, manual missing-line guard"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
