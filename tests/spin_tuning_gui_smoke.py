"""Real Tk bindings: wheel precision, auto/manual threshold, one auto-refit."""

from pathlib import Path
import tempfile
from tkinter import messagebox

import numpy as np

from gui_smoke import make_processed
from live_spin_gui_smoke import settled
from main import SpectrumApp
from spin_backend import data_snapshot, same_state
from spin_dialog import SpinDialog


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-tuning-") as directory:
            folder = Path(directory) / "Demo"
            make_processed(folder)
            app.add_spectrum_folder(folder)
            before = data_snapshot(app.spectra[app.active_spectrum_id])
            dialog = SpinDialog(app)
            dialog.attributes("-alpha", 0)
            app.update()
            panel = dialog.parameters
            panel.vars["fit.initial_width_hz"].set("5")
            entry = panel.entries["fit.initial_width_hz"]
            entry.event_generate("<MouseWheel>", delta=120)
            assert np.isclose(float(panel.vars["fit.initial_width_hz"].get()), 5.1)
            entry.event_generate("<MouseWheel>", delta=-120, state=1)
            assert np.isclose(float(panel.vars["fit.initial_width_hz"].get()), 5.09)
            for _ in range(4):
                entry.event_generate("<MouseWheel>", delta=30)
            assert np.isclose(float(panel.vars["fit.initial_width_hz"].get()), 5.19)
            panel.auto_threshold.set(True)
            panel.entries["fit.threshold"].event_generate("<MouseWheel>", delta=120)
            assert not panel.auto_threshold.get()
            panel.vars["baseline.method"].set("none")
            panel.auto_button.invoke()
            assert "Старт:" in panel.feedback.get()
            assert 3 < float(panel.vars["fit.initial_width_hz"].get()) < 9
            assert panel.auto_button.winfo_ismapped()
            dialog.run()
            app.on_x_selected(3.4, 3.7)
            workspace = app._spin_workspace
            settled(app, workspace)
            revision = workspace.revision
            workspace.parameters.auto_button.invoke()
            assert workspace.revision == revision + 1
            settled(app, workspace)
            assert len(workspace.states[workspace.active_sid]["models"][0]["centers"]) == 2
            workspace.parameters.vars["fit.min_width_hz"].set("1")
            workspace.parameters.vars["fit.max_width_hz"].set("1.01")
            workspace.parameters.entries["fit.min_width_hz"].event_generate(
                "<MouseWheel>", delta=120
            )
            assert float(workspace.parameters.vars["fit.min_width_hz"].get()) < 1.01
            workspace.destroy()
            assert same_state(before, data_snapshot(app.spectra[app.active_spectrum_id]))
            assert not errors, errors
            print(
                "TUNING GUI OK: wheel/Shift/partial deltas, manual threshold, auto estimates, one revision/refit, valid width bounds, preview isolation"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
