"""Actual plot events -> selected lines -> named outputs, without a phantom group."""

from pathlib import Path
import tempfile
from tkinter import messagebox

import numpy as np
from matplotlib.backend_bases import MouseEvent

from gui_smoke import make_processed
from live_spin_gui_smoke import settled
from main import SpectrumApp
from spin_dialog import SpinDialog
from spin_backend import data_snapshot, same_state


def mouse(workspace, name, x, y, key=None):
    pixel = workspace.top.transData.transform((x, y))
    event = MouseEvent(name, workspace.canvas, *pixel, button=1, key=key)
    workspace.canvas.callbacks.process(name, event)


def drag(workspace, low, high, key=None):
    workspace.canvas.draw()
    mouse(workspace, "button_press_event", low, 4000, key)
    mouse(workspace, "motion_notify_event", high, 4000, key)
    mouse(workspace, "button_release_event", high, 4000, key)
    workspace.app.update()


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-selection-") as directory:
            folder = Path(directory) / "Demo"
            make_processed(folder)
            app.add_spectrum_folder(folder)
            before = data_snapshot(app.spectra[app.active_spectrum_id])
            dialog = SpinDialog(app)
            dialog.attributes("-alpha", 0)
            dialog.vars["baseline.method"].set("none")
            dialog.vars["fit.threshold"].set("1500")
            dialog.run()
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            settled(app, w)
            assert w.plot_mode.get() == "click" and w.show_components.get()
            assert not w.select_region.get()
            w.plot_mode.set("region")
            w.set_selection_mode()
            assert len(w.group_specs) == 1
            assert w.tables.tab(w.lines_tab, "text") == "Линии модели (2)"
            assert w.tables.tab(w.output_tab, "text") == "Итоговые интегралы (1)"
            assert w.groups.winfo_height() > 70
            drag(w, 3.49, 3.51)
            assert w.selected_indices() == [0]
            w.top.set_xlim(3.64, 3.46)
            view = w.top.get_xlim()
            size = w.canvas.get_tk_widget().winfo_height()
            w.event_generate("<F11>")
            app.update()
            assert w.plot_focused and not w.tables.winfo_ismapped()
            assert w.canvas.get_tk_widget().winfo_height() > size
            assert w.selected_indices() == [0]
            np.testing.assert_allclose(w.top.get_xlim(), view)
            w.event_generate("<Escape>")
            app.update()
            assert w.tables.winfo_ismapped() and w.parameters.winfo_ismapped()
            assert w.selected_indices() == [0]
            np.testing.assert_allclose(w.top.get_xlim(), view)
            width = w.canvas.get_tk_widget().winfo_width()
            w.parameter_toggle.invoke()
            app.update()
            assert w.canvas.get_tk_widget().winfo_width() > width
            w.parameter_toggle.invoke()
            app.update()
            w.show_residual.set(True)
            w.layout_plot()
            assert w.residual.get_visible()
            assert w.top.get_position().y0 > w.residual.get_position().y1
            np.testing.assert_allclose(w.top.get_xlim(), view)
            w.show_residual.set(False)
            w.layout_plot()
            assert not w.residual.get_visible()
            sash = w.plot_split.sash_coord(0)
            size = w.canvas.get_tk_widget().winfo_height()
            w.plot_split.sash_place(0, sash[0], sash[1] + 25)
            app.update()
            assert w.canvas.get_tk_widget().winfo_height() > size
            assert w.selected_indices() == [0]
            np.testing.assert_allclose(w.top.get_xlim(), view)
            assert "1 линий" in w.selection_info.get()
            w.group_name.set("A")
            w.create_button.invoke()
            app.update()
            assert len(w.group_specs) == 1
            assert w.group_specs[0]["name"] == "A"
            assert w._whole_area_group_id is None
            assert sum(len(g["components"]) for g in w.states[w.active_sid]["groups"]) == 1
            drag(w, 3.57, 3.59, key="control")
            assert w.selected_indices() == [0, 1]
            drag(w, 3.57, 3.59)
            assert w.selected_indices() == [1]
            w.group_name.set("B")
            w.create_button.invoke()
            app.update()
            assert len(w.group_specs) == 2
            assert len(w.groups.get_children()) == 2
            assert "интегралы: 2" in w.apply_button.cget("text")
            w.groups.selection_set(w.group_specs[0]["id"])
            w.select_group_members()
            app.update()
            assert w.selected_indices() == [0]
            w.clear_component_selection()
            w.canvas.draw()
            model = w.states[w.active_sid]["models"][0]
            y = float(np.interp(3.58, model["x"], model["corrected"]))
            mouse(w, "button_press_event", 3.58, y)
            mouse(w, "button_release_event", 3.58, y)
            app.update()
            assert w.selected_indices() == [1]
            w.canvas.draw()
            mouse(w, "button_press_event", 3.5, 0)
            mouse(w, "button_release_event", 3.5, 0)
            app.update()
            assert not w.selected_indices()
            assert same_state(before, data_snapshot(app.spectra[app.active_spectrum_id]))
            w.apply()
            assert len(app.integrals) == 2
            np.testing.assert_allclose(
                [app.integral_area(i) for i in app.integrals],
                np.pi * np.array([72, 58.5]),
                rtol=0.02,
            )
            app.arm_spin_selection(app.spin_settings)
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            settled(app, w)
            w.select_component_region(3.49, 3.51)
            w.assign_selected()  # Explicitly edit the default result instead of creating one.
            assert w._whole_area_group_id is None
            w.select_component_region(3.57, 3.59)
            w.group_name.set("Second")
            w.create_group()
            assert len(w.group_specs) == 2  # The explicitly edited first result survives.
            w.destroy()
            assert not errors, errors
            print(
                "SELECTION GUI OK: model vs output counts, drag/Ctrl/click/clear, named outputs, no extra whole-area integral, membership display, full analytical areas"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
