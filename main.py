"""Himera entry point: NOX interface with an optional Spintegrate engine."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from himera_app import HimeraApp

    SpectrumApp = HimeraApp

__all__ = ["HimeraApp", "SpectrumApp"]


def __getattr__(name):
    # Preserve programmatic imports without delaying the splash on normal launch.
    if name in __all__:
        from himera_app import HimeraApp

        globals().update(HimeraApp=HimeraApp, SpectrumApp=HimeraApp)
        return HimeraApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    import sys

    from startup import launch

    raise SystemExit(launch(timing="--startup-timing" in sys.argv[1:]))
