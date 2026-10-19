"""A comparison cell opens the fit on its own spectrum, not the active record."""

from pathlib import Path
import os
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui_smoke import make_processed
from main import SpectrumApp


def pump(app, predicate):
    deadline = time.monotonic() + 25
    while not predicate():
        app.update()
        assert time.monotonic() < deadline, "Workspace did not settle"
        time.sleep(0.01)
    app.update()


def main():
    errors = []
    with tempfile.TemporaryDirectory(prefix="himera-spin-source-") as directory:
        root = Path(directory)
        os.environ["HIMERA_VIEW_PREFERENCES"] = str(root / "view.json")
        app = SpectrumApp()
        app.attributes("-alpha", 0)
        app.report_callback_exception = lambda kind, value, tb: errors.append(str(value))
        try:
            for name, scale in (("1", 1), ("2", 0.8)):
                make_processed(root / name, scale)
                assert app.add_spectrum_folder(root / name)
            first, second = app.spectra
            app.create_comparison([first, second], "common")
            app.open_spin_dialog()
            app.on_x_selected(3.4, 3.7)
            workspace = app._spin_workspace
            workspace.attributes("-alpha", 0)
            workspace.parameters.vars["baseline.method"].set("none")
            workspace.parameters.vars["fit.threshold"].set("1500")
            pump(app, workspace.ready)
            workspace.apply()
            app.update()
            assert app._spin_workspace is None
            app.restore_active_spectrum(first)
            app.draw_comparison()
            item = app.comparison_record()["integrals"][0]
            app.integral_inspector.select(item, sid=second)
            app.integral_inspector.edit_model()
            workspace = app._spin_workspace
            workspace.attributes("-alpha", 0)
            app.update()
            assert list(workspace.records) == [first, second]
            assert workspace.active_sid == second
            assert workspace.spectrum_box.current() == 1
            assert app.active_spectrum_id == first
            workspace.destroy()
            # Menu actions also respect the selected comparison row.
            app.integral_table.selection_set(f"s{second}")
            workspace = app.open_spin_diagnostics(item["spin_fit_id"])
            workspace.attributes("-alpha", 0)
            assert workspace.active_sid == second
            workspace.destroy()
            assert not errors, errors
            print(
                "Spin source GUI passed: comparison cell/menu → selected spectrum; series order unchanged"
            )
        finally:
            app.destroy()


if __name__ == "__main__":
    main()
