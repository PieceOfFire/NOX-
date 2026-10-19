"""Product workflow: simple fitting, actionable problems, pinned comparison and layout.

Run with --preview for a temporary synthetic workspace used for visual inspection.
"""

import copy
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import messagebox

import numpy as np

from gui_smoke import make_processed
from live_spin_gui_smoke import settled
from main import SpectrumApp
from spin_backend import same_state
from spin_review import compare_groups


def main():
    preview = "--preview" in sys.argv
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.geometry("1250x820+40+40")
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-product-") as directory:
            root = Path(directory)
            for name, scale in (("11", 1), ("13", 0.8)):
                make_processed(root / name, scale)
                (root / name / "pdata" / "1" / "title").write_text(
                    f"Катализатор Pd · опыт {name}\nСинтетический демонстрационный спектр",
                    encoding="utf-8",
                )
                assert app.add_spectrum_folder(root / name)
            app.create_comparison(list(app.spectra), "separate")
            originals = copy.deepcopy(dict(app.spectra))
            app.open_spin_dialog()
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            w.parameters.vars["baseline.method"].set("none")
            w.parameters.vars["fit.threshold"].set("1500")
            settled(app, w)
            assert w.parameters.mode.get() == "ordinary"
            assert w.comparison_states and w.problem_table.get_children() == ()
            sid = w.active_sid
            for index, name in ((0, "Сигнал A"), (1, "Сигнал B")):
                w.components.selection_set(str(index))
                w.group_name.set(name)
                w.create_group()
            w.capture_comparison()
            pinned = copy.deepcopy(w.comparison_states)
            assert all(
                np.isclose(row["delta_pct"], 0)
                for row in compare_groups(pinned[sid], w.states[sid])
            )
            w.show_before.set(True)
            w.plot()
            assert any("До ·" in line.get_label() for line in w.top.lines)
            # A genuine re-fit changes only preview states, never the pinned reference.
            w.set_group_shape(w.group_specs[0]["id"], "pseudo_voigt")
            settled(app, w)
            assert same_state(pinned, w.comparison_states)
            assert w.comparison_table.get_children()
            w.tables.select(w.comparison_tab)
            app.update()
            assert w.comparison_table.winfo_ismapped()
            w.parameters.vars["fit.threshold"].set("не число")
            from workspace_usability_gui_smoke import pump

            pump(app, lambda: w.debounce is None)
            assert w.issues[0]["kind"] == "parameter"
            w.open_problems()
            assert w.parameters.mode.get() == "expert"
            assert "Порог" in w.problem_hint.get() or "исправить" in w.problem_hint.get()
            assert str(w.apply_button.cget("state")) == "disabled"
            w.undo_edit()
            assert w.ready()
            # Drop both candidates through the real detector, preserving explicit groups.
            w.parameters.vars["fit.threshold"].set("1000000000")
            settled(app, w)
            lost = next(i for i in w.issues if i["kind"] == "missing")
            assert lost["centers"]
            w.open_problems()
            assert w.active_sid == lost["sid"]
            assert w.groups.selection()[0] == lost["group_id"]
            assert w.top.get_xlim()[0] > w.top.get_xlim()[1]
            assert str(w.apply_button.cget("state")) == "disabled"
            assert same_state(originals, dict(app.spectra))
            w.undo_edit()
            assert w.ready() and not any(i["kind"] == "missing" for i in w.issues)
            w.parameters.set_mode("ordinary")
            w.geometry("980x700+30+30")
            w.tables.select(w.output_tab)
            app.update()
            assert w.apply_button.winfo_ismapped() and w.groups.winfo_ismapped()
            assert w.canvas.get_tk_widget().winfo_height() >= 210
            assert (
                w.apply_button.winfo_rooty() + w.apply_button.winfo_height()
                <= w.winfo_rooty() + w.winfo_height()
            )
            w.geometry("1380x850+30+30")
            w.fit_plot()
            w.tables.select(w.comparison_tab)
            app.update()
            assert not errors, errors
            print(
                "PRODUCT GUI OK: simple mode, pinned comparison, problem targets, missing-line guard, preview isolation, small-window layout"
            )
            if preview:
                app.title("Himera · Проверка дизайна · временные спектры")
                w.title("Himera · Проверка дизайна Spintegrate")
                app.attributes("-alpha", 1)
                w.attributes("-alpha", 1)
                w.after(300000, app.destroy)
                app.mainloop()
    finally:
        try:
            app.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    main()
