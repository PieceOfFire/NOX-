"""Select a region first, then click dashed lines/groups and refit live."""

from pathlib import Path
import tempfile
from tkinter import messagebox

import numpy as np
from matplotlib.backend_bases import MouseEvent

from gui_smoke import make_processed
from live_spin_gui_smoke import settled
from main import SpectrumApp
from spin_backend import model_component, model_component_areas, data_snapshot, same_state
from spin_selection_gui_smoke import mouse


def click(workspace, x, y):
    workspace.canvas.draw()
    mouse(workspace, "button_press_event", x, y)
    mouse(workspace, "button_release_event", x, y)
    workspace.app.update()


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-click-") as directory:
            folder = Path(directory) / "Demo"
            make_processed(folder)
            app.add_spectrum_folder(folder)
            app.spin_button.invoke()
            assert app._spin_selecting and app._spin_workspace is None
            assert not app._spectrum_dialog_open
            app.spin_button.invoke()
            assert not app._spin_selecting
            app.open_spin_dialog()
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            w.attributes("-alpha", 0)
            w.parameters.vars["baseline.method"].set("none")
            w.parameters.vars["fit.threshold"].set("1500")
            settled(app, w)
            assert w.plot_mode.get() == "click" and not w.component_selector.active
            assert w.settings["fit"]["line_shape"] == "pseudo_voigt"
            assert w.settings["fit"]["refine_centers"]
            assert "100.00 %" in w.fit_score.get()
            model = w.states[w.active_sid]["models"][0]
            assert all(artist.get_linestyle() == "--" for artist in w.component_artists)
            click(w, 3.5, float(model_component(model, 0, np.array([3.5]))[0]))
            assert w.selected_indices() == [0]
            click(w, 3.5, float(model_component(model, 0, np.array([3.5]))[0]))
            assert not w.selected_indices()
            click(w, 3.5, float(model_component(model, 0, np.array([3.5]))[0]))
            click(w, 3.58, float(model_component(model, 1, np.array([3.58]))[0]))
            assert w.selected_indices() == [0, 1]
            w.group_name.set("AB")
            w.create_button.invoke()
            app.update()
            assert len(w.group_specs) == 1 and w.group_artists
            w.clear_component_selection()
            group_y = sum(float(model_component(model, i, np.array([3.5]))[0]) for i in (0, 1))
            click(w, 3.5, group_y)
            assert w.selected_indices() == [0, 1]
            assert w.selected_group() == w.group_specs[0]["id"]
            click(w, 3.5, group_y)
            assert not w.selected_indices()
            w.plot_mode.set("pan")
            w.set_selection_mode()
            click(w, 3.5, group_y)
            assert not w.selected_indices()
            w.plot_mode.set("click")
            w.set_selection_mode()
            w.canvas.draw()
            pixel = w.top.transData.transform((3.5, group_y))
            event = MouseEvent("button_press_event", w.canvas, *pixel, button=3)
            w.canvas.callbacks.process("button_press_event", event)
            assert not w.selected_indices()
            w.parameters.vars["fit.line_shape"].set("Гаусс")
            assert "пересчёт" in w.fit_score.get()
            settled(app, w)
            assert w.settings["fit"]["line_shape"] == "gaussian"
            assert w.states[w.active_sid]["models"][0]["diagnostics"]["r_squared"] < 0.999
            expected = sum(model_component_areas(w.states[w.active_sid]["models"][0]))
            before = data_snapshot(app.spectra[app.active_spectrum_id])
            app.axes.set_xlim(3.65, 3.45)
            app.axes.set_ylim(-1000, 120000)
            w.apply()
            app.update()
            assert len(app.integrals) == 1
            assert np.isclose(app.integral_area(app.integrals[0]), expected)
            assert app.axes.get_xlim() == (3.65, 3.45)
            assert app.axes.get_ylim()[1] < 20000
            assert same_state(before, data_snapshot(app.spectra[app.active_spectrum_id]))
            app.axes.set_ylim(-2000, 24000)
            app.draw()
            assert app.axes.get_ylim() == (-2000, 24000)  # Ordinary redraw preserves manual scale.
            assert not errors, errors
            print(
                "CLICK GUI OK: region first, dashed click/toggle, group clicks, pan/right-click guards, live shapes, R² and saved areas"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
