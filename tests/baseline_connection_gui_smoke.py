"""NOX correction -> automatic none; shared methods, undo and mixed-series policy."""

import copy
from pathlib import Path
import tempfile
from tkinter import messagebox

import numpy as np

from gui_smoke import make_processed
from live_spin_gui_smoke import settled
from main import SpectrumApp
from baseline_methods import MORE_OPTIONS, EXTENDED_METHODS
from nmr_dialogs import CorrectionDialog
from processing_templates import apply_baseline_steps
from spin_backend import same_state


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-baseline-link-") as directory:
            root = Path(directory)
            for name in ("1", "2"):
                make_processed(root / name)
                app.add_spectrum_folder(root / name)
            ids = list(app.spectra)
            app.activate_spectrum(ids[0])
            app.axes.set_xlim(3.7, 3.4)
            original = app.signal().copy()
            d = CorrectionDialog(app)
            d.attributes("-alpha", 0)
            assert d.method.get() == "Адаптивный arPLS"
            assert MORE_OPTIONS in d.method_box.cget("values")
            assert not d.auto_button.winfo_ismapped()
            app.update()
            # Hide the native popdown too; it has its own window opacity.
            popup = d.method_box.tk.call("ttk::combobox::PopdownWindow", str(d.method_box))
            d.tk.call("wm", "attributes", popup, "-alpha", 0)
            d.method_display.set(MORE_OPTIONS)
            d.method_box.event_generate("<<ComboboxSelected>>")
            app.update()
            assert d.methods_expanded
            assert len(d.method_box.cget("values")) == 13
            assert MORE_OPTIONS not in d.method_box.cget("values")
            assert d.tk.getboolean(d.tk.call("winfo", "ismapped", popup))
            assert not [w for w in d.winfo_children() if w.winfo_class() == "Toplevel"]
            d.method_box.tk.call("ttk::combobox::Unpost", str(d.method_box))
            assert d.method.get() == "Адаптивный arPLS"
            d.method_display.set(EXTENDED_METHODS["snip"])
            d.method_box.event_generate("<<ComboboxSelected>>")
            app.update()
            assert d.extended_parameters.winfo_ismapped()
            assert d.extended_parameters.field_widgets["half_window"][1].winfo_ismapped()
            assert not d.extended_parameters.field_widgets["lam"][1].winfo_ismapped()
            d.extended_parameters.fields["half_window"].set("-1")
            d.preview()
            assert "Некорректные параметры" in d.note.get()
            np.testing.assert_array_equal(app.signal(), original)
            d.extended_parameters.fields["half_window"].set("25")
            d.preview()
            assert d.method.get() == "Spintegrate: snip"
            assert d.extended_settings["half_window"] == 25
            for method in ("Фильтр Уиттакера", "Адаптивный arPLS", EXTENDED_METHODS["snip"]):
                d.method_display.set(method)
                d.method_box.event_generate("<<ComboboxSelected>>")
                app.update()
                assert d.extended_parameters.winfo_ismapped() == (
                    method == EXTENDED_METHODS["snip"]
                )
            assert d.extended_parameters.fields["half_window"].get() == "25"
            d.polarity.set("Положительные")
            d.preview()
            np.testing.assert_array_equal(app.signal(), original)
            d.commit()
            corrected = copy.deepcopy(app.spectra[ids[0]])
            assert corrected["baseline_steps"][0]["parameters"]["half_window"] == 25
            np.testing.assert_allclose(
                app.corrected, apply_baseline_steps(corrected, corrected["baseline_steps"])
            )
            app.undo_last_action()
            assert app.corrected is None
            app.redo_last_action()
            assert app.corrected is not None
            app.open_spin_dialog()
            app.on_x_selected(3.42, 3.68)
            w = app._spin_workspace
            settled(app, w)
            assert w.parameters.follow_nox.get()
            assert w.parameters.vars["baseline.method"].get() == "none"
            assert "NOX" in w.parameters.baseline_context_note.get()
            np.testing.assert_array_equal(w.states[w.active_sid]["models"][0]["baseline"], 0)
            w.parameters.vars["baseline.method"].set("snip")
            settled(app, w)
            assert not w.parameters.follow_nox.get()
            assert w.states[w.active_sid]["recipe"]["baseline_default"]["method"] == "snip"
            w.parameters.follow_nox.set(True)
            settled(app, w)
            assert w.parameters.vars["baseline.method"].get() == "none"
            w.parameters.auto_button.invoke()
            settled(app, w)
            assert w.parameters.vars["baseline.method"].get() == "none"
            w.destroy()
            app.create_comparison(ids, "separate")
            app.stop_spin_selection()
            app.open_spin_dialog()
            app.on_x_selected(3.42, 3.68)
            w = app._spin_workspace
            settled(app, w)
            assert w.states[ids[0]]["recipe"]["baseline_default"]["method"] == "none"
            assert w.states[ids[1]]["recipe"]["baseline_default"]["method"] == "snip"
            revision = w.revision
            w.spectrum_box.current(1)
            w.select_spectrum()
            assert w.parameters.vars["baseline.method"].get() == "snip"
            w.spectrum_box.current(0)
            w.select_spectrum()
            assert w.parameters.vars["baseline.method"].get() == "none"
            assert w.revision == revision
            w.destroy()
            assert same_state(corrected["baseline_steps"], app.spectra[ids[0]]["baseline_steps"])
            assert not errors, errors
            print(
                "BASELINE GUI OK: inline dropdown expansion/repost, no extra window, inline parameters/live preview, correction/undo, none after NOX, mixed-series defaults"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
