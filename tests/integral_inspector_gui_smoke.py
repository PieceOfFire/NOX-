"""Plot/table selection, editable properties, undo, comparisons and source focus."""

import copy
from pathlib import Path
import sys
import tempfile
from tkinter import messagebox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matplotlib.backend_bases import MouseEvent
from gui_smoke import make_processed
from main import SpectrumApp
from spin_backend import same_state


def main():
    errors = []
    original = messagebox.showinfo
    messagebox.showinfo = lambda *a, **k: errors.append(a)
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        app.update()
        with tempfile.TemporaryDirectory(prefix="himera-inspector-") as directory:
            root = Path(directory)
            for name, scale in (("1", 1), ("2", 0.8)):
                make_processed(root / name, scale)
                assert app.add_spectrum_folder(root / name)
            ids = list(app.spectra)
            app.activate_spectrum(ids[0])
            app.add_integral(3.45, 3.53)
            app.add_integral(3.54, 3.65)
            app.update()
            before = app.capture_spectrum_state()
            inspector = app.integral_inspector
            app.integral_table.selection_set("1")
            app.update()
            assert inspector.item_id == 1 and inspector.winfo_manager()
            inspector.name.set("Edited")
            inspector.low.set("3,46")
            inspector.apply()
            app.update()
            assert app.integrals[0]["left"] == 3.46 and app.integrals[0]["name"] == "Edited"
            app.undo_last_action()
            app.update()
            assert same_state(before, app.capture_spectrum_state())
            app.redo_last_action()
            app.update()
            assert app.integrals[0]["left"] == 3.46
            inspector.low.set("nan")
            inspector.apply()
            assert app.integrals[0]["left"] == 3.46
            inspector.revert()
            assert app.focus_integral(ids[0], 2)
            app.update()
            assert inspector.item_id == 2
            assert max(app.axes.get_xlim()) < 3.7

            # An ordinary region click synchronises table and inspector.
            app.axes.set_xlim(4, 3)
            app.canvas.draw()
            pixel = app.axes.transData.transform((3.5, 2000))
            event = MouseEvent("button_press_event", app.canvas, *pixel, button=1)
            app.canvas.callbacks.process("button_press_event", event)
            app.update()
            assert inspector.item_id == 1 and app.integral_table.selection() == ("1",)

            app.activate_spectrum(ids[1])
            app.add_integral(3.4, 3.7)
            app.update()
            app.integral_table.selection_set("1")
            app.update()
            assert inspector.context()[2]["id"] == ids[1]
            assert inspector.low.get().startswith("3.4")

            for mode in ("common", "separate"):
                app.create_comparison(ids, mode)
                app.add_comparison_integral(3.45, 3.53)
                comparison = app.comparison_record()
                comparison["offsets"][ids[1]] = 0.002
                app.draw_comparison()
                before_comparison = copy.deepcopy(comparison)
                assert app.focus_integral(ids[1], 1, comparison_id=comparison["id"])
                app.update()
                assert inspector.sid == ids[1] and inspector.item_id == 1
                inspector.low.set("3.46")
                inspector.apply()
                app.update()
                assert comparison["integrals"][0]["left"] == 3.46
                app.undo_last_action()
                app.update()
                assert same_state(before_comparison, app.comparison_record())
            assert not errors, errors
            print(
                "Integral inspector GUI passed: selection, source focus, precision edit, validation, undo, common/stacked comparison"
            )
    finally:
        messagebox.showinfo = original
        app.destroy()


if __name__ == "__main__":
    main()
