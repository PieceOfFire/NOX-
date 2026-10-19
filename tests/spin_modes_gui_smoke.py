"""Simple/expert presentation shares one recipe and never launches a refit."""

import copy
from pathlib import Path
import sys
import tkinter as tk

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline_methods import EXTENDED_METHODS
from spin_backend import default_recipe
from spin_dialog import SpinParameters
from spintegrate.profiles import SHAPE_LABELS
from ui_theme import install_theme


def main():
    root = tk.Tk()
    root.attributes("-alpha", 0)
    root.geometry("420x780")
    errors, changes, modes, auto = [], [], [], []
    root.report_callback_exception = lambda kind, value, tb: errors.append(str(value))
    install_theme(root)
    try:
        panel = SpinParameters(
            root, on_change=lambda: changes.append(True),
            on_auto=lambda: auto.append(True), on_mode_change=modes.append,
        )
        panel.pack(fill="both", expand=True)
        root.update()
        assert panel.mode.get() == "ordinary"
        assert panel.simple_panel.winfo_ismapped()
        assert panel.auto_button.winfo_ismapped()
        assert not panel.tabs.winfo_ismapped()
        assert not panel.entries["fit.threshold"].winfo_viewable()
        before = panel.collect_recipe()
        panel.mode_buttons["expert"].invoke()
        root.update()
        assert panel.tabs.winfo_ismapped()
        assert not panel.simple_panel.winfo_ismapped()
        assert panel.collect_recipe() == before
        assert changes == [] and modes == ["expert"]

        panel.vars["fit.threshold"].set("54321")
        panel.vars["fit.max_peaks"].set("12")
        panel.simple_shape_box.set(SHAPE_LABELS["voigt"])
        edited = panel.collect_recipe()
        assert edited["fit"]["line_shape"] == "voigt"
        change_count = len(changes)
        panel.mode_buttons["ordinary"].invoke()
        root.update()
        assert panel.collect_recipe() == edited
        assert len(changes) == change_count
        assert panel.simple_shape_box.get() == SHAPE_LABELS["voigt"]
        panel.auto_button.invoke()
        assert len(auto) == 1
        panel.set_mode("ordinary")  # no redundant mode callback
        assert modes == ["expert", "ordinary"]

        # NOX baseline policy stays active, visible and correctly resolved while
        # the expert method widgets are hidden.
        record = {
            "ppm": np.linspace(0, 10, 1001),
            "corrected": np.zeros(1001),
            "baseline_steps": [dict(method="Адаптивный arPLS", low=3.0, high=4.0)],
        }
        recipe = copy.deepcopy(default_recipe())
        recipe["baseline_default"]["method"] = "snip"
        recipe["baseline_policy"] = "nox"
        panel.set_recipe(recipe, notify=False)
        panel.set_baseline_context(record, 3.2, 3.8)
        assert panel.vars["baseline.method"].get() == "none"
        assert "Повторно не вычитается" in panel.baseline_summary.get()
        assert panel.mode.get() == "ordinary"

        # Moving to a raw member of a series restores the fallback, not none.
        record = dict(record, corrected=None)
        panel.set_baseline_context(record, 3.2, 3.8)
        assert panel.vars["baseline.method"].get() == "snip"
        assert EXTENDED_METHODS["snip"] in panel.baseline_summary.get()
        panel.set_mode("expert")
        panel.vars["baseline.method"].set("arpls")
        assert not panel.follow_nox.get()
        panel.set_mode("ordinary")
        assert EXTENDED_METHODS["arpls"] in panel.baseline_summary.get()
        assert panel.collect_recipe()["baseline_policy"] == "manual"

        assert not errors, errors
        print("SPIN MODES GUI OK: ordinary/expert, shared recipe, no hidden refit, NOX policy and fallback")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
