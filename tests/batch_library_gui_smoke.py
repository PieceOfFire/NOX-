"""Hidden synthetic queue/library: 100 items, filters, navigation and keyboard guards."""

from pathlib import Path
import sys
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import SpectrumApp
from spectrum_state import SpectrumState


def main():
    errors = []
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.geometry("1250x850")
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    try:
        x = np.linspace(6, 5, 128)
        for sid in range(1, 102):
            app.spectra[sid] = SpectrumState(
                id=sid,
                name=f"{sid}",
                bruker_title=f"Катализатор {sid}",
                ppm=x.copy(),
                real=1000 / (1 + ((x - 5.5) / 0.02) ** 2),
                imaginary=np.zeros(x.size),
                spectral_width_hz=500,
            )
        app.activate_spectrum(1)
        app.create_comparison([1, 2], "separate")
        app.start_manual_batch(
            list(range(1, 101)), reference_id=1, formula={}, options={"align": False}
        )
        session = app.batch_session
        session.item(1)["status"] = "confirmed"
        session.item(2)["status"] = "review"
        session.item(3).update(status="error", error="Тестовая ошибка")
        session.item(4)["status"] = "skipped"
        session.item(6).update(status="review", blocking_issues=["Потеряна линия"])
        app._refresh_batch_bar()
        app.update()
        library = app.spectrum_library
        assert library.filter_key == "queue"
        assert app.spectrum_list.size() == 100
        assert "1 / 100" in library.batch_progress.get()
        assert "Подтверждён" in app.spectrum_list.item("0", "values")[0]
        library.set_batch_filter("review")
        assert app._library_items == [("spectrum", 2), ("spectrum", 6)]
        library.set_batch_filter("errors")
        assert app._library_items == [("spectrum", 3), ("spectrum", 6)]
        library.set_batch_filter("skipped")
        assert app._library_items == [("spectrum", 4)]
        library.set_batch_filter("waiting")
        assert len(app._library_items) == 95
        library.search.set("Катализатор 99")
        assert app._library_items == [("spectrum", 99)]
        app.batch_next_attention()
        assert session.current_sid == app.active_spectrum_id == 2
        assert library.search.get() == "" and library.filter_key == "queue"
        assert "Проверить результат" in library.batch_details(2)
        assert "Потеряна линия" in library.batch_details(6)
        assert not app._batch_running
        assert session.item(2)["status"] == "review"
        app.batch_next_attention()
        assert session.current_sid == 3
        assert session.item(3)["status"] == "error"  # navigating never reruns it
        library.set_batch_filter("all")
        assert app.spectrum_list.size() == 103  # 101 spectra + section + comparison

        # Actual Tk key bindings are layout independent (Return/arrows only).
        calls = []
        app.batch_action = lambda: calls.append("confirm")
        app.batch_skip = lambda: calls.append("skip")
        app.batch_step = lambda direction: calls.append(direction)
        app.batch_open_item(2)
        app.spectrum_list.focus_force()
        app.update()
        app.spectrum_list.event_generate("<Control-Return>")
        app.spectrum_list.event_generate("<Control-Shift-Return>")
        app.spectrum_list.event_generate("<Alt-Right>")
        app.spectrum_list.event_generate("<Alt-Left>")
        app.update()
        assert calls == ["confirm", "skip", 1, -1], calls
        app.batch_open_item(5)
        app.spectrum_list.event_generate("<Control-Return>")
        app.update()
        assert calls == ["confirm", "skip", 1, -1] and not app._batch_running
        assert not session.item(5)["phase_confirmed"]

        entry = library.search_entry
        assert app._batch_shortcut(SimpleNamespace(widget=entry), "confirm") is None
        child = tk.Toplevel(app)
        child.attributes("-alpha", 0)
        field = ttk.Entry(child)
        field.pack()
        child.grab_set()
        assert app._batch_shortcut(SimpleNamespace(widget=app.spectrum_list), "skip") is None
        child.destroy()
        session.pause()
        app._refresh_batch_bar()
        assert "пауза" in library.batch_progress.get()
        app.restore_batch_queue(None)
        assert library.filter_key == "all" and not library.batch_controls.winfo_manager()
        assert app.spectrum_list.size() == 103
        assert not errors, errors
        print("Batch library GUI smoke: OK")
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
