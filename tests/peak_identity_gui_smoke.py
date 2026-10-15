"""Six real peaks plus tail ripple: auto count and shape changes agree in Tk."""

from pathlib import Path
import tempfile
from tkinter import messagebox

from live_spin_gui_smoke import settled
from main import SpectrumApp
from spin_backend import data_snapshot, same_state
from test_peak_detection_identity import noisy_six


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-peak-identity-") as directory:
            folder = Path(directory) / "Noise"
            pdata = folder / "pdata" / "1"
            pdata.mkdir(parents=True)
            r, _ = noisy_six()
            r["real"][::-1].astype("<f8").tofile(pdata / "1r")
            n = len(r["real"])
            (pdata / "procs").write_text(
                f"##$SI= {n}\n##$OFFSET= 1\n##$SW_p= {400 * n / (n - 1)}\n"
                "##$SF= 400\n##$DTYPP= 2\n##$BYTORDP= 0\n",
                encoding="ascii",
            )
            app.add_spectrum_folder(folder)
            original = data_snapshot(app.spectra[app.active_spectrum_id])
            app.open_spin_dialog()
            app.on_x_selected(0.005, 0.995)
            w = app._spin_workspace
            w.parameters.vars["baseline.method"].set("none")
            w.parameters.auto_button.invoke()
            settled(app, w)
            assert "линий-кандидатов 6" in w.parameters.feedback.get()
            assert len(w.states[w.active_sid]["models"][0]["centers"]) == 6
            w.components.selection_set("0", "1")
            w.group_name.set("Крайняя пара")
            w.create_group()
            gid = w.group_specs[0]["id"]
            for shape in ("asymmetric_pseudo_voigt", "phased_voigt", "voigt", "pseudo_voigt"):
                w.set_group_shape(gid, shape)
                settled(app, w)
                state = w.states[w.active_sid]
                assert len(state["models"][0]["centers"]) == 6
                assert state["groups"][0]["components"] == [0, 1]
                assert state["groups"][0]["missing"] == 0
                shapes = state["models"][0].get(
                    "line_shapes", [state["recipe"]["fit"]["line_shape"]] * 6
                )
                assert shapes[:2] == [shape, shape]
            threshold = w.parameters.vars["fit.threshold"].get()
            w.parameters.vars["fit.threshold"].set("100000")
            settled(app, w)
            assert w.states[w.active_sid]["groups"][0]["missing"] == 2
            assert str(w.apply_button.cget("state")) == "disabled"
            w.parameters.vars["fit.threshold"].set(threshold)
            settled(app, w)
            # True losses remain pending acknowledgement, not fabricated zeros.
            assert w.states[w.active_sid]["groups"][0]["missing"] == 2
            w.components.selection_set("0", "1")
            w.assign_selected()
            settled(app, w)
            assert w.states[w.active_sid]["groups"][0]["missing"] == 0
            assert str(w.apply_button.cget("state")) == "normal"
            w.apply()
            assert same_state(original, data_snapshot(app.spectra[app.active_spectrum_id]))
            w = app.open_spin_diagnostics()
            settled(app, w)
            w.parameters.auto_button.invoke()
            settled(app, w)
            assert w.states[w.active_sid]["groups"][0]["id"] == gid
            assert w.states[w.active_sid]["groups"][0]["components"] == [0, 1]
            assert not w.states[w.active_sid]["groups"][0]["missing"]
            w.destroy()
            assert not errors, errors
            print(
                "PEAK GUI OK: shared auto count=6, four profile switches preserve both lines, true-loss guard, manual repair, saved edit, unchanged NOX signal"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
