"""Startup remains lightweight and owns splash lifetime on every exit path."""

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import startup


def test_importing_entry_point_does_not_import_scientific_stack():
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys, main; assert not {'numpy', 'matplotlib', 'himera_app'} & sys.modules.keys()",
        ],
        cwd=Path(startup.__file__).parent,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


class FakeSplash:
    def __init__(self):
        self.updates = []
        self.closed = False

    def update(self, stage, completed):
        self.updates.append((stage, completed))

    def close(self):
        self.closed = True


def test_stage_markers_follow_real_imports():
    events = []
    splash = FakeSplash()
    factory = object()

    def load(name):
        assert len(splash.updates) == len(events) + 1
        events.append(name)
        return SimpleNamespace(HimeraApp=factory)

    with patch.object(startup.importlib, "import_module", side_effect=load):
        assert startup.load_application(splash) is factory
    assert events == [stage[0] for stage in startup.STAGES]
    assert [n for _, n in splash.updates] == list(range(5))


def test_splash_outlives_construction_and_first_layout():
    splash = FakeSplash()
    events = []

    class Application:
        def __init__(self):
            assert not splash.closed
            events.append("created")

        def update_idletasks(self):
            assert not splash.closed
            events.append("drawn")

        def mainloop(self):
            assert splash.closed
            events.append("mainloop")

    with (
        patch.object(startup, "SplashProcess", return_value=splash),
        patch.object(startup, "load_application", return_value=Application),
    ):
        assert startup.launch() == 0
    assert events == ["created", "drawn", "mainloop"]


@pytest.mark.parametrize(
    "error", [ModuleNotFoundError("missing numpy"), RuntimeError("init failed")]
)
def test_import_failure_closes_splash_and_reports_error(error):
    splash = FakeSplash()
    with (
        patch.object(startup, "SplashProcess", return_value=splash),
        patch.object(startup, "load_application", side_effect=error),
        patch.object(startup, "show_startup_error") as report,
    ):
        assert startup.launch() == 1
    assert splash.closed
    report.assert_called_once_with(error)


def test_cancellation_after_window_creation_cleans_up_application():
    splash = FakeSplash()
    splash.update = lambda *args: (_ for _ in ()).throw(startup.StartupCancelled())
    destroyed = []
    application = SimpleNamespace(
        update_idletasks=lambda: None, destroy=lambda: destroyed.append(True)
    )
    with (
        patch.object(startup, "SplashProcess", return_value=splash),
        patch.object(startup, "load_application", return_value=lambda: application),
    ):
        assert startup.launch() == 0
    assert destroyed == [True] and splash.closed


def test_application_can_still_start_if_splash_process_cannot_launch():
    with patch.object(startup.subprocess, "Popen", side_effect=OSError("unavailable")):
        splash = startup.SplashProcess()
    splash.update("test", 1)
    splash.close()
