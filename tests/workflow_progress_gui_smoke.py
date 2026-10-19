"""Thread-safe progress/Stop, partial honesty, diagnostic context and return link."""

import copy
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui_smoke import make_processed
from integral_stability import analyze_integral_stability
from main import SpectrumApp
from spectrum_state import same_state
from workflow_tools import ResultsWindow


def pump(app, predicate):
    end = time.monotonic() + 20
    while not predicate():
        app.update()
        assert time.monotonic() < end
        time.sleep(0.01)
    app.update()


def chosen(window):
    return window.displayed[int(window.table.selection()[0])]


def main():
    with tempfile.TemporaryDirectory(prefix="himera-progress-") as directory:
        directory = Path(directory)
        os.environ["HIMERA_VIEW_PREFERENCES"] = str(directory / "view.json")
        make_processed(directory / "1")
        make_processed(directory / "2", 0.8)
        app = SpectrumApp()
        app.attributes("-alpha", 0)
        errors = []
        app.report_callback_exception = lambda kind, value, tb: errors.append(
            (str(kind), str(value))
        )
        release = threading.Event()
        try:
            app.add_spectrum_folder(directory / "1")
            first = app.active_spectrum_id
            app.add_integral(3.45, 3.53)
            app.add_integral(3.54, 3.65)
            app.integral_table.selection_remove(app.integral_table.selection())
            before = copy.deepcopy(app.spectra[first].snapshot())
            entered = threading.Event()

            def controlled(record, items, **options):
                original_progress = options["progress"]

                def report(completed, total, label):
                    original_progress(completed, total, label)
                    if completed == 3 and not entered.is_set():
                        entered.set()
                        assert release.wait(10), "GUI did not request Stop"

                options["progress"] = report
                return analyze_integral_stability(record, items, **options)

            with patch(
                "integral_stability_view.analyze_integral_stability", side_effect=controlled
            ):
                window = app.open_integral_stability()
                window.attributes("-alpha", 0)
                window.stability_run()
                pump(app, lambda: entered.is_set() and window.progress_last[0] == 3)
                assert window.running and window.progress_last[1] == 26
                assert "Сценарий" in window.status.get()
                window.request_stop()
                assert window.winfo_exists() and window.stop.is_set()
                assert "Остановка" in window.status.get()
                release.set()
                pump(app, lambda: not window.running)
                assert window.status.get().startswith("Остановлено")
                assert [s["completed"] for s in window.summaries] == [2, 1]
                assert all(s["partial"] for s in window.summaries)
                assert all(
                    "Неполная" in window.table.item(iid, "values")[-1]
                    for iid in window.table.get_children()
                )
                assert len(window.rows) == 3
                assert same_state(before, app.spectra[first].snapshot())
                window.stability_run()  # Stop is reset, not latched for future jobs
                pump(app, lambda: not window.running)
                assert len(window.rows) == 26
                assert all(not s["partial"] for s in window.summaries)
                assert window.progress_last[:2] == (26, 26)
                window.table.selection_set("1")
                window.stability_open_selected()
                assert not app._spectrum_dialog_open
                assert app.integral_inspector.item_id == app.integrals[1]["id"]

            # A normal results refresh does not destroy the selected rows/scroll.
            table_window = ResultsWindow(app, "test", ("A", "B"))
            table_window.attributes("-alpha", 0)
            table_window.set_rows([(n, n) for n in range(100)])
            app.update()
            table_window.table.selection_set("40", "41")
            table_window.table.focus("41")
            table_window.table.yview_moveto(0.4)
            app.update()
            scroll = table_window.table.yview()[0]
            table_window.set_rows([(n, n * 2) for n in range(100)])
            app.update()
            assert table_window.table.selection() == ("40", "41")
            assert table_window.table.focus() == "41"
            assert abs(table_window.table.yview()[0] - scroll) < 0.03
            table_window.table.heading(1, text="New schema")
            table_window.set_rows([(n, n) for n in range(100)])
            assert not table_window.table.selection()
            table_window.destroy()

            app.add_spectrum_folder(directory / "2")
            second = app.active_spectrum_id
            app.create_comparison([first, second], "common")
            app.add_comparison_integral(3.45, 3.53)
            app.add_comparison_integral(3.54, 3.65)
            comparison = app.comparison_record()
            comparison_id = comparison["id"]
            app.integral_inspector.select(comparison["integrals"][1], sid=second)
            window = app.open_integral_stability()
            window.attributes("-alpha", 0)
            window.stability_run()
            pump(app, lambda: not window.running)
            assert len(window.summaries) == 1
            assert (window.summaries[0]["spectrum_id"], window.summaries[0]["number"]) == (
                second,
                2,
            )
            window.stability_toggle_details()
            assert (chosen(window)["spectrum_id"], chosen(window)["number"]) == (second, 2)
            window.stability_open_selected()
            assert app.current_comparison_id == comparison_id
            assert app.integral_inspector.sid == second

            app.integral_table.selection_remove(app.integral_table.selection())
            app.integral_inspector.item_id, app.integral_inspector._key = None, None
            app.update()
            window = app.open_integral_stability()
            window.attributes("-alpha", 0)
            window.stability_run()
            pump(app, lambda: not window.running)
            assert len(window.summaries) == 4
            window.table.selection_set("3")
            window.stability_toggle_details()
            assert (chosen(window)["spectrum_id"], chosen(window)["number"]) == (second, 2)
            window.stability_toggle_details()
            assert (chosen(window)["spectrum_id"], chosen(window)["number"]) == (second, 2)
            window.destroy()
            assert not errors, errors
        finally:
            release.set()
            app.destroy()
    print("workflow_progress_gui_smoke: PASS")


if __name__ == "__main__":
    main()
