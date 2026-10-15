"""Optional read-only checks on existing user data; nothing is saved back."""

import argparse
from dataclasses import asdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from tkinter import filedialog, messagebox
from main import SpectrumApp
from spin_backend import data_snapshot, run_selected_region, same_state
from spintegrate.core import BaselineSettings, FitSettings, ProcessingSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path)
    parser.add_argument("--spectrum", type=Path)
    args = parser.parse_args()
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        if args.project and args.project.is_file():
            filedialog.askopenfilename = lambda **kw: str(args.project)
            app.open_project()
            assert not errors, errors
            assert app.spectra
            if app.formula_document.blocks:
                assert app.calculation_rows()
            print(
                f"Legacy NOX project OK: {len(app.spectra)} spectra, {len(app.comparisons)} comparisons"
            )
        if args.spectrum:
            assert app.add_spectrum_folder(args.spectrum)
            sid = app.active_spectrum_id
            record = app.spectra[sid]
            original = data_snapshot(record)
            params = dict(
                source="current",
                auto_threshold=True,
                processing=asdict(ProcessingSettings()),
                fit=asdict(FitSettings(restarts=2, max_nfev=1000, max_peaks=100)),
                baseline_default=asdict(BaselineSettings(method="arpls")),
                regions=[
                    dict(
                        name="Проверка",
                        low=1.1,
                        high=2.7,
                        baseline=asdict(BaselineSettings(method="arpls")),
                    )
                ],
            )
            spin = run_selected_region(record, params, 1.1, 2.7)
            assert len(spin["models"][0]["centers"]) > 0
            assert same_state(original, data_snapshot(record))
            app.apply_spin_region(
                {sid: spin}, dict(fit_id="test-legacy-fit", left=1.1, right=2.7, comparison_id=None)
            )
            assert np.isfinite(app.integral_area(app.integrals[-1]))
            assert same_state(original, data_snapshot(app.spectra[sid]))
            assert not errors, errors
            print(
                f"Existing processed spectrum OK: {len(spin['models'][0]['centers'])} lines; "
                f"relative RMSE {spin['models'][0]['diagnostics']['relative_rmse']:.4g}; "
                "source arrays unchanged"
            )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
