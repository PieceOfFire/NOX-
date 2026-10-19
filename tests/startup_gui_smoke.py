"""Real splash, cold scientific imports, main-window ownership and EOF cleanup."""

import io
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from startup import SplashProcess, StartupCancelled, load_application


def main():
    started = time.perf_counter()
    splash = SplashProcess(hidden=True)
    child = splash.process
    assert child is not None
    app = None
    try:
        timings = []
        Application = load_application(splash, timing=lambda *args: timings.append(args))
        imported = time.perf_counter() - started
        created_at = time.perf_counter()
        app = Application()
        constructed = time.perf_counter() - created_at
        app.attributes("-alpha", 0)
        layout_at = time.perf_counter()
        app.update_idletasks()
        layout = time.perf_counter() - layout_at
        assert not {'scipy', 'nmrglue', 'pybaselines', 'spintegrate.core'} & sys.modules.keys()
        assert len(timings) == 4 and all(elapsed >= 0 for _, elapsed in timings)
        # Splash and working window have separate Tcl interpreters/default roots.
        assert app.integration_method._tk is app.tk
        assert app.winfo_width() > 900
        splash.update("Готово", 5)
        splash.close()
        ready = time.perf_counter() - started
        assert child.poll() == 0
        assert splash.process is None
        app.update()
    finally:
        splash.close()
        if app is not None:
            app.destroy()

    from startup_splash import SplashWindow

    errors = []
    # Slow input simulates heavy imports; animation must keep moving independently.
    import os

    reader_fd, writer_fd = os.pipe()
    reader = os.fdopen(reader_fd, encoding="utf-8")
    writer = os.fdopen(writer_fd, "w", encoding="utf-8")
    window = SplashWindow(reader, hidden=True)
    window.report_callback_exception = lambda kind, value, tb: errors.append(str(value))
    try:
        writer.write('{"stage":"Проверка", "completed":2}\n')
        writer.flush()
        window.after(450, window.quit)
        window.mainloop()
        assert window.tick >= 3 and window.stage.get() == "Проверка"
        assert window.completed == 2 and window.stage_updates == 1
        writer.close()  # Also represents the parent process disappearing.
        window.mainloop()
        assert not errors, errors
    finally:
        reader.close()
        if not writer.closed:
            writer.close()

    # A visible window's X reports cancellation distinctly from a splash failure.
    cancelled = SplashWindow(io.StringIO(""), hidden=True)
    cancelled.cancel()
    assert cancelled.exit_code == 20
    fake_child = subprocess.Popen([sys.executable, "-c", "raise SystemExit(20)"])
    fake_child.wait(timeout=5)
    progress = SplashProcess.__new__(SplashProcess)
    progress.process = fake_child
    try:
        progress.check_cancelled()
        raise AssertionError("Cancellation was not propagated")
    except StartupCancelled:
        pass
    finally:
        progress.process = None
    print(
        f"STARTUP GUI OK: fresh-process imports {imported:.2f}s, "
        f"construction {constructed:.2f}s, layout {layout:.2f}s, ready {ready:.2f}s; "
        "independent animation, EOF cleanup, cancellation"
    )


if __name__ == "__main__":
    main()
