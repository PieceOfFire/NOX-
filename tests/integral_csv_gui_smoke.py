"""Actual CSV dialog export scopes; temporary public arrays and no interactive windows."""

import copy
import csv
from pathlib import Path
import sys
import tempfile
from tkinter import filedialog, ttk

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integral_csv import DISCRETE, integration_value
from main import SpectrumApp
from spectrum_state import SpectrumState, same_state


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def read_csv(path):
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.reader(stream, delimiter=";"))


def number(value):
    return float(value.replace(",", "."))


def main():
    errors, paths = [], []
    original_save = filedialog.asksaveasfilename
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.geometry("1250x850")
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-integral-csv-") as folder:
            folder = Path(folder)

            def choose(**kwargs):
                assert kwargs["defaultextension"] == ".csv"
                path = folder / f"export-{len(paths)}.csv"
                paths.append(path)
                return str(path)

            filedialog.asksaveasfilename = choose
            x = np.linspace(0, 10, 1001)
            for sid, scale in ((1, 1), (2, 2), (3, 0)):
                app.spectra[sid] = SpectrumState(
                    id=sid,
                    name=f"Спектр {sid}",
                    ppm=x.copy(),
                    real=scale * (1 + 2 * x),
                    imaginary=np.zeros_like(x),
                    spectral_width_hz=500,
                    integrals=[
                        dict(id=1, number=1, name="Продукт", left=2, right=8, color="#e45756")
                    ],
                    next_integral_id=2,
                )
            app.activate_spectrum(1)
            app.update()
            before = copy.deepcopy(dict(app.spectra))

            def export(scope, display="Площадь", bounds=False):
                window = app.export_integrals_csv(scope=scope)
                assert window is not None
                window.attributes("-alpha", 0)
                window.csv_display.set(display)
                if bounds:
                    button = next(
                        w
                        for w in descendants(window)
                        if isinstance(w, ttk.Checkbutton)
                        and w.cget("text").startswith("Добавить названия")
                    )
                    button.invoke()
                app.update()
                window.csv_save()
                app.update()
                assert not window.winfo_exists(), (
                    app.status.get(),
                    [
                        window.getvar(str(w.cget("textvariable")))
                        for w in descendants(window)
                        if isinstance(w, ttk.Label) and w.cget("textvariable")
                    ],
                )
                assert "CSV сохранён" in app.status.get()
                return read_csv(paths[-1])

            rows = export("current", bounds=True)
            assert rows[1][0] == "Спектр 1" and len(rows) == 2
            assert number(rows[1][rows[0].index("I1")]) == 66
            assert rows[1][rows[0].index("I1: имя")] == "Продукт"
            rows = export("all", display=DISCRETE)
            assert [row[0] for row in rows[1:]] == ["Спектр 1", "Спектр 2", "Спектр 3"]
            assert number(rows[3][rows[0].index("I1")]) == 0
            assert same_state(before, dict(app.spectra))

            app.create_comparison([1, 2], "common")
            comparison = app.comparison_record()
            comparison["offsets"][2] = 0.25
            app.add_comparison_integral(2.25, 8.25)
            rows = export("current")
            assert len(rows) == 3
            item = comparison["integrals"][0]
            for index, sid in enumerate((1, 2), 1):
                expected = integration_value(
                    app.spectra[sid], item, offset=comparison["offsets"][sid]
                )
                assert np.isclose(number(rows[index][rows[0].index("I1")]), expected)
            assert same_state(before, dict(app.spectra))

            app.start_manual_batch([1, 2], reference_id=1, formula={}, options={"align": False})
            app.batch_session.item(1)["status"] = "confirmed"
            app._refresh_batch_bar()
            rows = export("batch")
            assert len(rows) == 3 and rows[1][1] == "Подтверждён"
            assert rows[2][1] == "Нужна ручная фаза"
            assert same_state(before, dict(app.spectra))

            # Cancelling the file picker keeps the export options open, with no write.
            filedialog.asksaveasfilename = lambda **kwargs: ""
            window = app.export_integrals_csv()
            window.attributes("-alpha", 0)
            window.csv_save()
            assert window.winfo_exists() and len(paths) == 4
            window.destroy()
            assert not errors, errors
        print("Integral CSV GUI smoke: OK (current, all, comparison offsets, batch, cancel)")
    finally:
        filedialog.asksaveasfilename = original_save
        app.destroy()


if __name__ == "__main__":
    main()
