"""Remember inspection context and layout without changing a scientific record."""

from pathlib import Path
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui_smoke import make_processed
from main import SpectrumApp


def main():
    with tempfile.TemporaryDirectory(prefix="himera-view-") as directory:
        directory = Path(directory)
        os.environ["HIMERA_VIEW_PREFERENCES"] = str(directory / "view.json")
        make_processed(directory / "1")
        make_processed(directory / "2", 10)
        app = SpectrumApp()
        app.attributes("-alpha", 0)
        errors = []
        app.report_callback_exception = lambda kind, value, tb: errors.append(
            (str(kind), str(value))
        )
        try:
            app.geometry("1250x850+30+30")
            app.update()
            assert app.add_spectrum_folder(directory / "1")
            first = app.active_spectrum_id
            app.axes.set_xlim(3.62, 3.42)
            assert app.add_spectrum_folder(directory / "2")
            second = app.active_spectrum_id
            np.testing.assert_allclose(app.axes.get_xlim(), (3.62, 3.42))
            high = app.axes.get_ylim()[1]
            app.activate_spectrum(first)
            np.testing.assert_allclose(app.axes.get_xlim(), (3.62, 3.42))
            assert high > 5 * app.axes.get_ylim()[1]

            app.linked_ppm_view.set(False)
            app.auto_y_on_switch.set(False)
            app.axes.set_ylim(2, 13)
            app.activate_spectrum(second)
            app.axes.set_xlim(3.95, 3.72)
            app.axes.set_ylim(-99, 999)
            app.activate_spectrum(first)
            np.testing.assert_allclose(app.axes.get_xlim(), (3.62, 3.42))
            np.testing.assert_allclose(app.axes.get_ylim(), (2, 13))
            app.activate_spectrum(second)
            np.testing.assert_allclose(app.axes.get_xlim(), (3.95, 3.72))
            np.testing.assert_allclose(app.axes.get_ylim(), (-99, 999))

            app.activate_spectrum(first)
            app.integrals = [
                {
                    "id": n,
                    "number": n,
                    "name": f"Test {n}",
                    "left": 3.43,
                    "right": 3.62,
                    "color": "#e45756",
                }
                for n in range(1, 36)
            ]
            app.show_integral_table()
            app.draw()
            app.update()
            app.integral_table.selection_set(("3", "11"))
            app.integral_table.focus("11")
            app.integral_table.yview_moveto(0.5)
            app.integral_table.column("name", width=205)
            app.update()
            before = app.integral_table.yview()[0]
            column_width = app.integral_table.column("name", "width")
            app.refresh_integrals()
            app.update()
            assert app.integral_table.selection() == ("3", "11")
            assert abs(app.integral_table.yview()[0] - before) < 0.04
            assert app.integral_table.column("name", "width") == column_width
            app.activate_spectrum(second)
            app.integrals = [dict(item) for item in app.spectra[first]["integrals"]]
            app.refresh_integrals()
            assert not app.integral_table.selection(), "Different spectra reused integral IDs"
            app.activate_spectrum(first)
            assert app.integral_table.selection() == ("3", "11")

            app.library_split.sash_place(0, 300, 0)
            app.spectrum_split.sash_place(0, 0, int(app.spectrum_split.winfo_height() * 0.6))
            app.update()
            saved = app.capture_view_layout()
            app.library_visible.set(False)
            app.toggle_library_panel()
            assert str(app.library_panel) not in tuple(map(str, app.library_split.panes()))
            assert not errors, errors
        finally:
            app.destroy()

        restored = SpectrumApp()
        restored.attributes("-alpha", 0)
        try:
            restored.update()
            assert not restored.linked_ppm_view.get()
            assert not restored.auto_y_on_switch.get()
            assert not restored.library_visible.get()
            assert str(restored.library_panel) not in tuple(
                map(str, restored.library_split.panes())
            )
            assert restored._integral_table_visible
            assert abs(restored.graph_height_percent - saved["graph_percent"]) < 2
            assert restored.winfo_width() == 1250
        finally:
            restored.destroy()
    print("view_preferences_gui_smoke: PASS")


if __name__ == "__main__":
    main()
