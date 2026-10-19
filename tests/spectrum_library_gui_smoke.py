"""Hidden synthetic Bruker library: selection, filtering, status refresh and persistence."""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui_smoke import make_processed
from main import SpectrumApp
from spin_backend import data_snapshot


def main():
    errors = []
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-library-") as folder:
            root = Path(folder)
            for name, title in (
                ("11", "Pd-катализатор\nПосле реакции"),
                ("13", "Контрольный образец"),
            ):
                make_processed(root / name)
                (root / name / "pdata" / "1" / "title").write_text(title, encoding="utf-8")
                assert app.add_spectrum_folder(root / name)
            app.update()
            assert app.spectrum_list.size() == 2
            assert app.spectrum_list.curselection() == (1,)
            assert app.spectrum_list.get(0).startswith("11 · Pd-катализатор")
            assert app.spectra[1]["name"] == "11"

            # A status-only refresh must not clear undo or reactivate a spectrum.
            app.add_integral(3.4, 3.7)
            count = len(app._undo_stack)
            app.phase0 = 3
            app.draw()
            app.update()
            assert "Фаза" in app.spectrum_list.item("1", "values")[0]
            assert len(app._undo_stack) == count

            app.spectrum_library.search.set("Pd после")
            app.update()
            assert app.spectrum_list.size() == 1
            assert app.spectrum_list.curselection() == ()
            app.spectrum_list.selection_set(0)
            app.on_spectrum_selected()
            app.update()
            assert app.active_spectrum_id == 1
            assert "Pd-катализатор" in app.spectrum_library._full_details
            app.spectrum_library.search.set("")
            app.update()
            assert app.spectrum_list.curselection() == (0,)
            app.create_comparison([1, 2], "separate")
            app.update()
            assert app.spectrum_list.size() == 4
            assert app.spectrum_list.curselection() == (3,)
            assert "11, 13" in app.spectrum_library._full_details

            app.activate_spectrum(1)
            record = app.spectra[1]
            record["spin_analyses"] = {
                "f": {"expected": data_snapshot(record), "models": [{"centers": []}], "groups": []}
            }
            record["last_spin_fit"] = "f"
            # Test library independently of drawing an intentionally minimal fake fit.
            app.refresh_spectrum_library()
            assert "Подгонка" in app.spectrum_list.item("0", "values")[0]
            record["phase0"] += 1
            app.refresh_spectrum_library()
            app.update()
            assert "Пересчитать" in app.spectrum_list.item("0", "values")[0]
            record["spin_analyses"] = {}
            record["last_spin_fit"] = None

            payload = app.project_spectrum_data(record)
            assert payload["bruker_title"] == "Pd-катализатор\nПосле реакции"
            record["bruker_title"] = "Changed"
            app.restore_project_spectrum_payload(payload)
            assert record["bruker_title"] == payload["bruker_title"]
            # Embedded loads retain the title even when original files are unavailable.
            source = root / "unavailable" / "pdata" / "1"
            app._embedded_sources[str(source.resolve())] = payload
            assert app.add_spectrum_folder(source)
            assert app.spectra[app.active_spectrum_id]["bruker_title"] == payload["bruker_title"]
            app.update()
            assert not errors, errors
        print("Spectrum library GUI smoke: OK")
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
