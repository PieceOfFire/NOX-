"""Saved fit plots do not shift groups or inherit unrelated Y ranges."""

from types import SimpleNamespace

import numpy as np
from matplotlib.figure import Figure

from himera_app import HimeraApp
from spin_backend import model_component
from spin_groups import group_area
from test_spin_groups import state


def test_saved_groups_are_zero_based_and_background_is_not_added_per_group():
    record, fit = state()
    model = fit["models"][0]
    model["baseline"] = np.linspace(-500, -100, len(model["x"]))
    before = [group_area(record, fit, g["id"]) for g in fit["groups"]]
    items = [
        dict(spin_fit_id="fit", spin_group_id=g["id"], number=i + 1, name=g["name"], color=color)
        for i, (g, color) in enumerate(zip(fit["groups"], ["red", "blue"]))
    ]
    app = SimpleNamespace(comparison_record=lambda: None, integrals=items)
    axes = Figure().subplots()
    HimeraApp.draw_spin_groups(app, axes, record, fit, "fit")
    for index, artist in enumerate(axes.lines):
        np.testing.assert_allclose(artist.get_ydata(), model_component(model, index))
        assert np.min(artist.get_ydata()) >= 0
        assert artist.get_linestyle() == "--"
    assert before == [group_area(record, fit, g["id"]) for g in fit["groups"]]
    stacked = Figure().subplots()
    HimeraApp.draw_spin_groups(
        app, stacked, record, fit, "fit", transform=lambda y: y * 0.00001 + 2.42
    )
    assert all(np.min(line.get_ydata()) >= 2.42 for line in stacked.lines)


def test_y_fit_uses_visible_curves_ignores_outside_peaks_and_keeps_x():
    axes = Figure().subplots()
    x = np.linspace(3, 4, 4001)
    signal = 2e6 * np.exp(-(((x - 3.5) / 0.015) ** 2)) + 6e6 * np.exp(-(((x - 3.85) / 0.01) ** 2))
    axes.plot(x, signal)
    axes.plot(x, np.linspace(-3e5, -1e5, len(x)), ":")
    axes.axvline(3.4)
    axes.axvspan(3.4, 3.6, alpha=0.1)
    axes.set_xlim(3.6, 3.4)
    axes.set_ylim(-1e6, 7e6)
    redraws = []
    app = SimpleNamespace(
        axes=axes,
        comparison_record=lambda: None,
        canvas=SimpleNamespace(draw_idle=lambda: redraws.append(True)),
    )
    HimeraApp.fit_visible_plot_y(app)
    assert axes.get_xlim() == (3.6, 3.4)
    low, high = axes.get_ylim()
    assert -5e5 < low < -2e5
    assert 2e6 < high < 2.3e6
    assert redraws
    previous = axes.get_ylim()
    app.comparison_record = lambda: {"mode": "separate"}
    axes.plot(x, signal * 20)
    HimeraApp.fit_visible_plot_y(app)
    assert axes.get_ylim() == previous


def test_y_fit_handles_flat_and_empty_views_without_changing_data():
    axes = Figure().subplots()
    values = np.full(10, 5.0)
    axes.plot(np.arange(10), values)
    axes.set_xlim(7, 2)
    app = SimpleNamespace(
        axes=axes, comparison_record=lambda: None, canvas=SimpleNamespace(draw_idle=lambda: None)
    )
    HimeraApp.fit_visible_plot_y(app)
    assert axes.get_ylim()[0] < 5 < axes.get_ylim()[1]
    np.testing.assert_array_equal(axes.lines[0].get_ydata(), values)
    axes.set_xlim(30, 20)
    previous = axes.get_ylim()
    HimeraApp.fit_visible_plot_y(app)
    assert axes.get_ylim() == previous
