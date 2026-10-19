"""Workspace transactions, selection/view preservation and in-flight undo."""

import copy
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
from tkinter import messagebox

import numpy as np

from gui_smoke import make_processed
from live_spin_gui_smoke import settled
from main import SpectrumApp
from spin_backend import data_snapshot, same_state
import spin_workspace


def pump(app, predicate, timeout=15):
    until = time.monotonic() + timeout
    while not predicate():
        app.update()
        assert time.monotonic() < until
        time.sleep(0.01)
    app.update()


def main():
    errors = []
    messagebox.showerror = lambda title, text, **kw: errors.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.report_callback_exception = lambda kind, value, tb: errors.append((str(kind), str(value)))
    original_runner = spin_workspace.run_selected_region
    release = threading.Event()
    try:
        with tempfile.TemporaryDirectory(prefix="himera-workspace-") as directory:
            root = Path(directory)
            for name, scale in (("1", 1), ("2", 0.6)):
                make_processed(root / name, scale)
                app.add_spectrum_folder(root / name)
            app.create_comparison(list(app.spectra), "separate")
            originals = {sid: data_snapshot(r) for sid, r in app.spectra.items()}
            app.open_spin_dialog()
            app.on_x_selected(3.4, 3.7)
            w = app._spin_workspace
            w.attributes("-alpha", 0)
            w.parameters.vars["baseline.method"].set("none")
            w.parameters.vars["fit.threshold"].set("1500")
            settled(app, w)
            sid = w.active_sid
            w.components.selection_set("0")
            w.top.set_xlim(3.64, 3.43)
            w.top.set_ylim(-100, 9000)
            w.plot()
            np.testing.assert_allclose(w.top.get_ylim(), [-100, 9000])
            w.parameters.vars["fit.initial_width_hz"].set("7")
            settled(app, w)
            assert w.selected_indices() == [0]
            np.testing.assert_allclose(w.top.get_ylim(), [-100, 9000])
            w.spectrum_box.current(1)
            w.select_spectrum()
            assert w.selected_indices() == []
            w.top.set_ylim(-200, 3000)
            w.spectrum_box.current(0)
            w.select_spectrum()
            assert w.active_sid == sid and w.selected_indices() == [0]
            np.testing.assert_allclose(w.top.get_ylim(), [-100, 9000])
            w.fit_plot()
            assert w.top.get_ylim()[1] > 9000
            w.zoom_plot()
            assert w.navigation.mode.name == "ZOOM" and not w.component_selector.active
            w.plot_mode.set("click")
            w.set_selection_mode()
            assert not w.navigation.mode
            w.group_name.set("Первый")
            w.create_group()
            gid = w.group_specs[0]["id"]
            assert len(w.group_specs) == 1
            w.undo_edit()
            assert w.group_specs[0]["name"] == "Вся область"
            w.redo_edit()
            assert w.group_specs[0]["id"] == gid
            w.groups.selection_set(gid)
            w.delete_group()
            assert not w.group_specs
            w.undo_edit()
            assert w.group_specs[0]["id"] == gid
            w.set_group_shape(gid, "asymmetric_pseudo_voigt")
            settled(app, w)
            fitted = copy.deepcopy(w.states)
            w.undo_edit()
            assert not w.states[sid]["groups"][0].get("line_shape")
            w.redo_edit()
            assert same_state(w.states, fitted)
            w.parameters.vars["fit.threshold"].set("invalid")
            pump(app, lambda: w.debounce is None)
            assert not w.ready() and "Порог поиска" in w.status.get()
            w.undo_edit()
            assert w.ready() and same_state(w.states, fitted)
            w.parameters.vars["fit.max_peaks"].set("1")
            pump(app, lambda: w.debounce is None and not w.running)
            assert not w.ready() and "Подгонка не готова" in w.status.get()
            w.undo_edit()
            assert w.ready() and same_state(w.states, fitted)
            w.parameters.vars["fit.threshold"].set("1000000000")
            settled(app, w)
            assert w.states[sid]["groups"][0]["missing"]
            w.undo_edit()
            assert not w.states[sid]["groups"][0]["missing"]
            assert same_state(w.states, fitted)
            # Queue a delayed worker then cancel its revision with local undo.
            started = threading.Event()

            def delayed(*args, **kw):
                started.set()
                assert release.wait(15)
                return original_runner(*args, **kw)

            spin_workspace.run_selected_region = delayed
            w.parameters.vars["fit.initial_width_hz"].set("9")
            pump(app, started.is_set)
            w.undo_edit()
            assert w.ready() and same_state(w.states, fitted)
            release.set()
            pump(app, lambda: not w.running)
            assert same_state(w.states, fitted)
            # Both keyboard layouts: local history never reaches the main project.
            app.update()
            w.focus_force()
            app.update()
            w.event_generate("<KeyPress>", keycode=90, state=4)
            app.update()
            assert not w.states[sid]["groups"][0].get("line_shape")
            w.history_key(SimpleNamespace(keysym="Cyrillic_ya", keycode=90, state=5))
            assert same_state(w.states, fitted)
            w.parameters.set_mode("expert")
            app.update()
            parameter_entry = w.parameters.entries["fit.threshold"]
            parameter_entry.focus_force()
            app.update()
            parameter_entry.event_generate("<KeyPress>", keycode=90, state=4)
            app.update()
            assert not w.states[sid]["groups"][0].get("line_shape")
            parameter_entry.event_generate("<KeyPress>", keycode=90, state=5)
            app.update()
            assert same_state(w.states, fitted)
            # Small windows still expose the last advanced parameter by scrolling.
            w.geometry("980x700")
            page = w.parameters.pages[-1]
            w.parameters.tabs.select(page)
            app.update()
            page.canvas.yview_moveto(1)
            app.update()
            entry = w.parameters.entries["fit.min_prominence"]
            assert (
                page.canvas.winfo_rooty()
                <= entry.winfo_rooty()
                < page.canvas.winfo_rooty() + page.canvas.winfo_height()
            )
            w.apply()
            for spectrum_id, record in app.spectra.items():
                assert same_state(originals[spectrum_id], data_snapshot(record))
            assert not errors, errors
            print(
                "WORKSPACE GUI OK: history, invalid-input recovery, stale-worker rejection, view/selection per spectrum, shape undo, scrolling, commit isolation"
            )
    finally:
        release.set()
        spin_workspace.run_selected_region = original_runner
        app.destroy()


if __name__ == "__main__":
    main()
