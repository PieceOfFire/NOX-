"""Batch setup with 100 duplicate-name synthetic records and a 980 px window.

Only the setup is exercised; no batch workers or 100-spectrum processing runs.
"""

import copy
from pathlib import Path
import sys
import tempfile
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from batch_workflow import BatchSetup
from gui_smoke import make_processed
from main import SpectrumApp


def button(parent, label):
    for child in parent.winfo_children():
        if isinstance(child, ttk.Button) and child.cget("text") == label:
            return child
        found = button(child, label)
        if found is not None:
            return found
    return None


def main():
    callbacks, dialogs, defects = [], [], []
    original_showerror = messagebox.showerror
    messagebox.showerror = lambda title, text, **kw: dialogs.append((title, text))
    app = SpectrumApp()
    app.attributes("-alpha", 0)
    app.geometry("980x760")
    app.report_callback_exception = lambda kind, value, tb: callbacks.append((str(kind), str(value)))
    try:
        with tempfile.TemporaryDirectory(prefix="himera-batch-setup-") as directory:
            root = Path(directory)
            make_processed(root / "seed")
            assert app.add_spectrum_folder(root / "seed")
            seed = copy.deepcopy(app.spectra[app.active_spectrum_id])
            app.active_spectrum_id = None
            app.spectra.clear()
            ids = list(range(101, 201))
            for sid in ids:
                record = copy.deepcopy(seed)
                record.update(
                    id=sid, name="Одинаковое название", bruker_title="",
                    source_folder=root / f"synthetic-{sid}",
                )
                app.spectra[sid] = record
            app._next_spectrum_id = 201
            app.activate_spectrum(ids[49])
            assert app.apply_calculation_preset("builtin:ratio")
            app.update()

            setup = BatchSetup(app)
            setup.attributes("-alpha", 0)
            app.update()
            assert len(setup.table.get_children()) == 100
            assert {int(iid) for iid in setup.table.selection()} == set(ids)
            assert len(setup.selected) == 100
            labels = setup.reference.cget("values")
            assert len(labels) == 100 and len(set(labels)) == 1
            assert setup.selected[setup.reference.current()] == ids[49]

            # Removing a different item changes indices. The chosen sample ID
            # must survive that change even when every display label is equal.
            expected_reference = ids[73]
            setup.reference.current(setup.selected.index(expected_reference))
            setup.table.selection_remove(str(ids[0]))
            app.update()
            actual = setup.selected[setup.reference.current()]
            if actual != expected_reference:
                defects.append(
                    f"Duplicate-name sample changed after deselecting another row: "
                    f"expected ID {expected_reference}, got ID {actual}"
                )
            select_all = button(setup, "Выбрать все")
            assert select_all is not None
            select_all.invoke()
            app.update()
            assert {int(iid) for iid in setup.table.selection()} == set(ids)
            actual = setup.selected[setup.reference.current()]
            if actual != expected_reference:
                defects.append(
                    f"Duplicate-name sample changed after Select all: "
                    f"expected ID {expected_reference}, got ID {actual}"
                )

            setup.reference.current(setup.selected.index(expected_reference))
            setup.formula.current(0)  # Explicitly override the existing formula.
            assert setup.formula.get() == "Без формулы"
            setup.align.set(False)
            setup.start_button.invoke()
            app.update()
            assert not setup.winfo_exists()
            session = app.batch_session
            assert session is not None and len(session.items) == 100
            if session.reference_id != expected_reference:
                defects.append(
                    f"Setup started with wrong duplicate-name sample: "
                    f"expected ID {expected_reference}, got ID {session.reference_id}"
                )
            assert set(session.spectrum_ids) == set(ids)
            assert session.current_sid == session.reference_id
            assert session.current_item["status"] == "reference"
            assert session.formula == {} and session.options == {"align": False}
            assert session.template is None and not app._batch_running
            assert all(not item["phase_confirmed"] for item in session.items)

            # Check actual allocation, not only whether pack reports a manager:
            # Tk can shrink a button to a few pixels when a row does not fit.
            assert app.winfo_width() == 980, app.winfo_geometry()
            row = app.batch_action_button.master
            buttons = [child for child in row.winfo_children() if isinstance(child, ttk.Button)]
            assert len(buttons) == 6
            geometry = []
            for item in buttons:
                label = item.cget("text")
                actual_width, requested_width = item.winfo_width(), item.winfo_reqwidth()
                mapped = bool(item.winfo_ismapped())
                geometry.append((label, actual_width, requested_width, mapped))
                left = item.winfo_rootx()
                right = left + actual_width
                if (
                    not mapped or actual_width < requested_width
                    or left < row.winfo_rootx()
                    or right > row.winfo_rootx() + row.winfo_width()
                ):
                    defects.append(
                        f"Batch bar at 980 px clips {label!r}: "
                        f"actual={actual_width}px, requested={requested_width}px, "
                        f"mapped={mapped}, row={row.winfo_width()}px"
                    )
            print(f"Batch setup: 100 selected, sample ID={session.reference_id}, no formula, no worker")
            print(f"Batch bar button geometry (text, actual, requested, mapped): {geometry!a}")
            assert not callbacks, callbacks
            assert not dialogs, dialogs
            assert not defects, "\n".join(defects)
            print("Batch setup GUI smoke passed: duplicate-name IDs, Select all, no formula, 980 px bar")
    finally:
        messagebox.showerror = original_showerror
        app.destroy()


if __name__ == "__main__":
    main()
