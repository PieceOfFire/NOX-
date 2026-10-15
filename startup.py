"""Lightweight entry path; show feedback before importing the scientific stack."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys
import traceback

STAGES = (
    ("numpy", "Загрузка вычислительных библиотек…"),
    ("matplotlib.backends.backend_tkagg", "Подготовка графического модуля…"),
    ("alignment", "Загрузка обработки спектров…"),
    ("himera_app", "Подготовка расчётов и интерфейса…"),
)


class StartupCancelled(Exception):
    pass


class SplashProcess:
    """Independent Tk loop: heavy imports cannot freeze the loading animation."""

    def __init__(self, *, hidden=False):
        self.process = None
        command = [sys.executable, "-B", str(Path(__file__).with_name("startup_splash.py"))]
        if hidden:
            command.append("--hidden")
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except OSError:
            # An unavailable splash must not prevent starting the application.
            pass

    def check_cancelled(self):
        if self.process is not None and self.process.poll() == 20:
            raise StartupCancelled()

    def update(self, stage, completed):
        self.check_cancelled()
        if self.process is None or self.process.poll() is not None:
            return
        try:
            self.process.stdin.write(json.dumps({"stage": stage, "completed": completed}) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        # EOF closes the splash on success, error, or an unexpectedly lost parent.
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=2)


def load_application(progress):
    """Stage markers describe actual imports, not an estimated time percentage."""
    application = None
    for index, (module, label) in enumerate(STAGES):
        progress.update(label, index)
        application = importlib.import_module(module)
    progress.update("Создание рабочего окна…", len(STAGES))
    return application.HimeraApp


def show_startup_error(error):
    """Keep failures visible even when started by double-click or pythonw."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        try:
            detail = str(error)
            if isinstance(error, ModuleNotFoundError):
                detail += "\n\nУстановите зависимости Himera в используемое окружение из requirements.txt."
            messagebox.showerror("Himera · Ошибка запуска", detail, parent=root)
        finally:
            root.destroy()
    except Exception:
        pass  # The original traceback remains available in the terminal.


def launch():
    splash = SplashProcess()
    application = None
    try:
        application_class = load_application(splash)
        application = application_class()
        # Complete initial layout and the first plot before removing the splash.
        application.update_idletasks()
        splash.update("Всё готово", len(STAGES) + 1)
        splash.close()
    except StartupCancelled:
        if application is not None:
            application.destroy()
        return 0
    except Exception as error:
        traceback.print_exc()
        if application is not None:
            application.destroy()
        splash.close()
        show_startup_error(error)
        return 1
    finally:
        splash.close()
    application.mainloop()
    return 0
