"""Hidden Tk checks for one spectrum state across NOX, Spintegrate and history."""

import copy
from pathlib import Path
import tempfile
from tkinter import filedialog, messagebox
from unittest.mock import patch

import numpy as np

from gui_smoke import make_processed
from main import SpectrumApp
from nmr_dialogs import CorrectionDialog, PhaseDialog
from spin_backend import data_snapshot, fit_current, run_selected_region, same_state
from test_spin_bridge import recipe


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        assert app.ppm is None and app.corrected is None and not app.spectra
        with tempfile.TemporaryDirectory(prefix="himera-canonical-state-") as directory:
            root = Path(directory)
            for name, scale in (("A", 1.0), ("B", 0.8)):
                make_processed(root / name, scale)
                assert app.add_spectrum_folder(root / name)
            a, b = list(app.spectra)
            app.activate_spectrum(a)
            clean_a = copy.deepcopy(app.spectra[a])

            # These assertions deliberately have no intervening save, draw or event loop.
            app.phase0 = 1.25
            app.phase1 = -2.5
            app.phase_pivot = 0.4
            app.ppm_shift = 0.001
            app._title = "Canonical A"
            app._next_integral_id = 17
            for key, expected in (
                ("phase0", 1.25),
                ("phase1", -2.5),
                ("phase_pivot", 0.4),
                ("ppm_shift", 0.001),
                ("title", "Canonical A"),
                ("next_integral_id", 17),
            ):
                assert app.spectra[a][key] == expected, key
            corrected = app.real.copy() + 5
            x = app.ppm.copy() + 0.001
            app.corrected = corrected
            app.ppm = x
            assert app.spectra[a]["corrected"] is corrected
            assert app.spectra[a]["ppm"] is x
            app.integration_method.set("Сумма модулей относительно 0")
            app.integration_display.set("Сумма дискретных точек (MestReNova)")
            assert app.spectra[a]["integration_method"] == app.integration_method.get()
            assert app.spectra[a]["integration_display"] == app.integration_display.get()

            app.spectra[a]["phase0"] = 4.5
            app.spectra[a]["corrected"] = None
            app.spectra[a]["title"] = "Record A"
            app.spectra[a]["integration_method"] = "Обычная площадь"
            app.spectra[a]["integration_display"] = "Площадь"
            assert app.phase0 == 4.5 and app.corrected is None and app._title == "Record A"
            assert app.integration_method.get() == "Обычная площадь"
            assert app.integration_display.get() == "Площадь"
            app.save_active_spectrum()
            assert app.integration_choice.get() == "Обычная площадь"
            assert app.integration_display_choice.get() == "Площадь"

            replacement = copy.deepcopy(app.spectra[a])
            replacement.update(phase0=7.5, title="Replacement A")
            replacement["real"] = replacement["real"] * 0.95
            app.spectra[a] = replacement
            assert app.phase0 == 7.5 and app._title == "Replacement A"
            assert app.real is app.spectra[a]["real"]
            app.phase0 = 8.5
            assert app.spectra[a]["phase0"] == 8.5

            app.integration_method.set("Сумма модулей относительно 0")
            app.integration_display.set("Сумма дискретных точек (MestReNova)")
            app.activate_spectrum(b)
            assert app.phase0 == 0 and app.corrected is None
            assert app.integration_method.get() == "Обычная площадь"
            assert app.integration_display.get() == "Площадь"
            app.phase0 = -1.75
            app.activate_spectrum(a)
            assert app.phase0 == 8.5 and app.spectra[b]["phase0"] == -1.75
            assert app.integration_method.get() == "Сумма модулей относительно 0"
            assert app.integration_display.get() == "Сумма дискретных точек (MestReNova)"

            # Start a reproducible correction and fit from the untouched synthetic input.
            app.spectra[a] = clean_a
            app.restore_active_spectrum(a)
            app.axes.set_xlim(3.7, 3.4)
            correction = CorrectionDialog(app)
            correction.attributes("-alpha", 0)
            correction.commit()
            assert app.corrected is app.spectra[a]["corrected"]
            assert app.spectra[a]["baseline_steps"]
            state = run_selected_region(app.spectra[a], recipe(), 3.4, 3.7)
            assert len(state["models"][0]["centers"]) == 2
            groups = [
                dict(id="left", name="Left", components=[0], missing=0),
                dict(id="right", name="Right", components=[1], missing=0),
            ]
            state["groups"] = groups
            app.apply_spin_region(
                {a: state},
                dict(
                    fit_id="canonical-fit",
                    comparison_id=None,
                    left=3.4,
                    right=3.7,
                    groups=[dict(id=g["id"], name=g["name"]) for g in groups],
                ),
            )

            def current_fit():
                record = app.spectra[a]
                return fit_current({**record, "spin": record["spin_analyses"]["canonical-fit"]})

            assert current_fit()
            initial_areas = [app.integral_area(item) for item in app.integrals]
            assert np.isfinite(initial_areas).all()
            app.apply_calculation_preset("builtin:ratio")
            ratio = app.calculation_rows()[0][1][0][2].value
            assert np.isclose(ratio, initial_areas[0] / initial_areas[1])

            before_phase = app.capture_spectrum_state()
            dialog = PhaseDialog(app)
            dialog.attributes("-alpha", 0)
            dialog.p0.set(dialog.p0.get() + 3)
            assert app.corrected is None and app.spectra[a]["corrected"] is None
            assert not current_fit()
            dialog.cancel()
            assert same_state(before_phase, app.capture_spectrum_state())
            assert current_fit()
            np.testing.assert_allclose([app.integral_area(i) for i in app.integrals], initial_areas)

            dialog = PhaseDialog(app)
            dialog.attributes("-alpha", 0)
            dialog.p0.set(dialog.p0.get() + 2)
            dialog.done()
            assert app.corrected is None and not app.spectra[a]["baseline_steps"]
            assert not current_fit()
            after_phase = app.capture_spectrum_state()
            app.undo_last_action()
            assert current_fit() and same_state(before_phase, app.capture_spectrum_state())
            app.redo_last_action()
            assert not current_fit() and same_state(after_phase, app.capture_spectrum_state())
            app.undo_last_action()
            assert current_fit()

            # Changes to raw quadrature or phase convention cannot reuse an old model.
            for key in ("real", "imaginary", "phase_pivot", "spectral_width_hz"):
                record = app.spectra[a]
                original = record[key]
                record[key] = original + (0.001 if key == "phase_pivot" else 1)
                assert not current_fit(), key
                record[key] = original
                assert current_fit(), key

            before_shift = app.capture_spectrum_state()
            with patch("nox_app.simpledialog.askfloat", return_value=3.505):
                app.calibrate_shift(3.5)
            assert current_fit()
            assert np.isclose(app.ppm_shift, 0.005)
            np.testing.assert_allclose(
                app.ppm, np.asarray(before_shift["ppm"]) + 0.005, rtol=0, atol=1e-12
            )
            np.testing.assert_allclose([app.integral_area(i) for i in app.integrals], initial_areas)
            for old, new in zip(before_phase["baseline_steps"], app.spectra[a]["baseline_steps"]):
                assert np.isclose(new["low"], old["low"] + 0.005)
                assert np.isclose(new["high"], old["high"] + 0.005)
            shifted = app.capture_spectrum_state()
            app.undo_last_action()
            assert same_state(before_shift, app.capture_spectrum_state()) and current_fit()
            app.redo_last_action()
            assert same_state(shifted, app.capture_spectrum_state()) and current_fit()

            # Embedded data, fit identities, comparison and formulas survive a full reload.
            app.create_comparison([a, b], "separate")
            comparison = app.comparison_record()
            comparison["integrals"] = copy.deepcopy(app.spectra[a]["integrals"])
            comparison["next_integral_id"] = app.spectra[a]["next_integral_id"]
            # Missing fits in B are valid persisted data, not an excuse to drop A's groups.
            expected_records = copy.deepcopy(dict(app.spectra))
            expected_formulas = app.formula_preset_data()
            path = root / "canonical.nmrproj.json"
            app.write_project(path)
            assert path.is_file() and not errors, errors
            with patch.object(filedialog, "askopenfilename", return_value=str(path)):
                app.open_project()
            assert len(app.spectra) == 2 and app.comparison_record()["mode"] == "separate"
            for sid, expected in expected_records.items():
                actual = app.spectra[sid]
                assert same_state(data_snapshot(expected), data_snapshot(actual))
                assert expected["integrals"] == actual["integrals"]
                assert same_state(
                    expected.get("baseline_steps", []), actual.get("baseline_steps", [])
                )
                assert same_state(
                    expected.get("spin_analyses", {}), actual.get("spin_analyses", {})
                )
                assert actual["integration_method"] == expected["integration_method"]
                assert actual["integration_display"] == expected["integration_display"]
            assert app.formula_preset_data() == expected_formulas
            app.activate_spectrum(a)
            assert current_fit()
            np.testing.assert_allclose([app.integral_area(i) for i in app.integrals], initial_areas)

            # Clearing the library must detach old arrays before another spectrum is added.
            app.spectra.clear()
            assert app.active_spectrum_id is None
            assert app.ppm is None and app.real is None and app.corrected is None
            assert app.load_spectrum_folder(root / "B")
            assert app.phase0 == 0 and app.corrected is None and not app.integrals
            assert not errors, errors
            print(
                "CANONICAL GUI OK: immediate bidirectional state, record replacement, spectrum isolation, "
                "phase cancel/commit/history, fit invalidation, X shift, embedded project/formula roundtrip, clear/reload"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
