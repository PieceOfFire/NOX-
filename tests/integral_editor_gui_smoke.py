"""Real canvas dragging: limits, formulas, undo/redo, comparisons and escape."""

import copy
from pathlib import Path
import tempfile
import time
from tkinter import messagebox

import numpy as np
from matplotlib.backend_bases import MouseEvent

from gui_smoke import make_processed
from main import SpectrumApp
from spectrum_state import same_state


def event(app, name, ppm, *, button=1):
    app.canvas.draw()
    x, y = app.axes.transData.transform((ppm, np.mean(app.axes.get_ylim())))
    return MouseEvent(name, app.canvas, x, y, button=button)


def send(app, name, ppm):
    ev = event(app, name, ppm)
    app.canvas.callbacks.process(name, ev)
    return ev


def drag(app, start, finish):
    send(app, "button_press_event", start)
    assert app.integral_boundary_editor.dragging
    send(app, "motion_notify_event", finish)
    send(app, "button_release_event", finish)
    app.update()


def main():
    errors = []
    original = messagebox.showerror
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.geometry("1200x800")
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-integral-editor-") as directory:
            root = Path(directory)
            for name, scale in (("1", 1), ("2", 0.6)):
                make_processed(root / name, scale)
                assert app.add_spectrum_folder(root / name)
            app.add_integral(3.4, 3.55)
            app.add_integral(3.56, 3.7)
            app.apply_calculation_preset("builtin:ratio")
            app.update()
            app.clear_history()
            old = app.capture_spectrum_state()
            before = app.calculation_rows()[0][1][0][2].value
            app.toggle_integral_boundaries()
            editor = app.integral_boundary_editor
            assert editor.active and not app.span.active
            assert len(editor.handles) == 4
            original_count = len(app.integrals)
            app.on_x_selected(3.2, 3.3)
            assert len(app.integrals) == original_count
            drag(app, 3.4, 3.495)
            assert not editor.dragging
            assert len(app._undo_stack) == 1
            assert app.integrals[0]["left"] > 3.49
            after = app.calculation_rows()[0][1][0][2].value
            assert not np.isclose(before, after)
            changed = app.capture_spectrum_state()
            app.undo_last_action()
            assert same_state(old, app.capture_spectrum_state())
            app.redo_last_action()
            assert same_state(changed, app.capture_spectrum_state())

            # Crossing the other edge clamps to exactly enough original points.
            drag(app, app.integrals[0]["left"], 3.8)
            item = app.integrals[0]
            assert item["left"] < item["right"]
            assert np.count_nonzero((app.ppm >= item["left"]) & (app.ppm <= item["right"])) >= 3
            app.undo_last_action()

            previous = app.capture_spectrum_state()
            history = len(app._undo_stack)
            send(app, "button_press_event", app.integrals[0]["left"])
            send(app, "motion_notify_event", 3.42)
            # The debounced live plot rebuild must preserve the active drag.
            until = time.monotonic() + 2
            while editor._pending is not None:
                app.update()
                assert time.monotonic() < until
            assert not same_state(previous, app.capture_spectrum_state())
            assert editor.dragging and not app.span.active
            assert editor.escape() == "break"
            assert editor.active and not editor.dragging
            assert same_state(previous, app.capture_spectrum_state())
            assert len(app._undo_stack) == history
            # Ctrl+Z during a drag cancels only that drag, not an earlier action.
            send(app, "button_press_event", app.integrals[0]["left"])
            send(app, "motion_notify_event", 3.43)
            app.undo_last_action()
            assert same_state(previous, app.capture_spectrum_state())
            assert len(app._undo_stack) == history

            # Fitted groups are not silently converted to numerical integration.
            model_item = copy.deepcopy(app.integrals[1])
            model_item.update(id=50, number=3, left=3.25, right=3.3,
                              spin_fit_id="unavailable", spin_group_id="group")
            app.integrals.append(model_item)
            app.draw()
            assert len(editor.handles) == 4
            send(app, "button_press_event", 3.25)
            assert not editor.dragging and "Spintegrate" in app.status.get()
            assert model_item["left"] == 3.25
            # Shared model-domain edges must not hide an ordinary area's handle.
            model_item["left"] = app.integrals[0]["left"]
            old_left = model_item["left"]
            app.draw()
            send(app, "button_press_event", old_left)
            assert editor.dragging and editor.drag.item is app.integrals[0]
            send(app, "motion_notify_event", old_left - .01)
            assert model_item["left"] == old_left
            editor.cancel_drag()
            model_item = next(i for i in app.integrals if i["id"] == 50)
            app.integrals.remove(model_item)

            # Both comparison display modes share editable integration ranges.
            for mode in ("common", "separate"):
                app.create_comparison(list(app.spectra), mode)
                app.add_comparison_integral(3.4, 3.7)
                comparison = app.comparison_record()
                comparison["offsets"][list(app.spectra)[1]] = 0.01
                app.draw_comparison()
                app.clear_history()
                previous = app.comparison_integral_state(comparison)
                app.toggle_integral_boundaries()
                drag(app, 3.4, 3.48)
                assert len(app._undo_stack) == 1
                assert comparison["integrals"][0]["left"] > 3.47
                app.undo_last_action()
                assert previous == app.comparison_integral_state(comparison)
                app.redo_last_action()
                assert comparison["integrals"][0]["left"] > 3.47

            # Switching spectra exits the mode and restores an unfinished drag.
            send(app, "button_press_event", comparison["integrals"][0]["left"])
            original_left = comparison["integrals"][0]["left"]
            send(app, "motion_notify_event", 3.45)
            app.activate_spectrum(list(app.spectra)[0])
            assert not editor.active and not editor.dragging
            assert comparison["integrals"][0]["left"] == original_left
            app.toggle_integral_boundaries()
            app.toggle_integration()
            assert not editor.active and app.integration_mode and app.span.active
            assert not errors, errors
            print("Integral boundary editor GUI smoke passed")
    finally:
        app.destroy()
        messagebox.showerror = original


if __name__ == "__main__":
    main()
