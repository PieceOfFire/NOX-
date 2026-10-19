"""Direct manipulation of ordinary integration limits on the spectrum plot.

The editor changes the existing record in place and records one transaction per
drag. Model-group areas deliberately stay owned by the Spintegrate workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk

import numpy as np


def boundary_limits(grids, left, right, edge, *, minimum_samples=3):
    """Allowed range of an edge, preserving enough samples in every spectrum.

    Grids are in displayed coordinates (including comparison offsets). They may
    be ascending or descending; limits must remain ordered, never swap places.
    """
    if edge not in {"left", "right"} or minimum_samples < 2:
        raise ValueError("Invalid integration boundary")
    allowed = []
    for grid in grids:
        x = np.sort(np.asarray(grid, dtype=float).ravel())
        x = x[np.isfinite(x)]
        if len(x) < minimum_samples:
            return None
        if edge == "left":
            within = x[x <= right]
            if len(within) < minimum_samples:
                return None
            allowed.append((float(x[0]), float(within[-minimum_samples])))
        else:
            within = x[x >= left]
            if len(within) < minimum_samples:
                return None
            allowed.append((float(within[minimum_samples - 1]), float(x[-1])))
    if not allowed:
        return None
    low = max(a for a, _ in allowed)
    high = min(b for _, b in allowed)
    return (low, high) if low <= high else None


def moved_boundary(grids, left, right, edge, proposed):
    """Clamp an edge to the sampled domain; None means no valid placement."""
    if not np.isfinite(proposed):
        return None
    limits = boundary_limits(grids, left, right, edge)
    if limits is None:
        return None
    return float(np.clip(proposed, *limits))


@dataclass
class _Drag:
    item: dict
    edge: str
    previous: dict
    comparison: dict | None
    grids: list
    context: tuple


class IntegralBoundaryEditor:
    """Canvas controller; call refresh_handles after rebuilding the plot axes."""

    def __init__(self, app, button):
        self.app = app
        self.button = button
        self.active = False
        self.drag = None
        self.handles = []
        self._hover = None
        self._pending = None
        self._connections = [
            app.canvas.mpl_connect("button_press_event", self.on_press),
            app.canvas.mpl_connect("motion_notify_event", self.on_motion),
            app.canvas.mpl_connect("button_release_event", self.on_release),
            app.canvas.mpl_connect("figure_leave_event", self.on_leave),
        ]
        app.bind("<Escape>", self.escape, add="+")

    @property
    def dragging(self):
        return self.drag is not None

    def context(self):
        app = self.app
        if app.current_comparison_id is not None:
            return ("comparison", app.current_comparison_id)
        return ("spectrum", app.active_spectrum_id)

    def items(self):
        comparison = self.app.comparison_record()
        return list(comparison.get("integrals", [])) if comparison else self.app.integrals

    @staticmethod
    def editable(item):
        # A model integral is a set of complete fitted lines, not a trapezoid.
        return not item.get("spin_fit_id") and not item.get("spin_group_id")

    def toggle(self):
        self.set_active(not self.active)

    def set_active(self, active):
        active = bool(active)
        if not active:
            self.cancel_drag()
        self.active = active
        if active:
            self.app.integration_mode = self.app.x_zoom_mode = False
            self.app.integration_button.configure(relief="raised")
            self.app.zoom_button.configure(relief="raised")
            self.app.show_integral_table()
            if self.app.span is not None:
                self.app.span.set_active(False)
            self.app.status.set(
                "Границы интегралов: тяните вертикальную линию или ромб. "
                "Esc — отменить перетаскивание / выйти. Интегралы Spintegrate — через S."
            )
        self.button.configure(relief="sunken" if active else "raised")
        self._set_cursor("")
        self.refresh_handles()
        self.app.canvas.draw_idle()

    def _set_cursor(self, cursor):
        try:
            self.app.canvas.get_tk_widget().configure(cursor=cursor)
        except tk.TclError:
            pass

    def refresh_handles(self):
        for _, _, line in self.handles:
            try:
                line.remove()
            except (ValueError, NotImplementedError):
                pass
        self.handles.clear()
        if not self.active:
            return
        for item in self.items():
            if not self.editable(item):
                continue
            for edge in ("left", "right"):
                line, = self.app.axes.plot(
                    [float(item[edge])] * 2,
                    [0.0, 0.985],
                    transform=self.app.axes.get_xaxis_transform(),
                    color=item.get("color", "#2468b4"),
                    linewidth=1.8,
                    alpha=0.9,
                    linestyle="--",
                    marker="D",
                    markevery=[1],
                    markersize=6,
                    markeredgecolor="white",
                    zorder=20,
                    scalex=False,
                    scaley=False,
                )
                self.handles.append((item, edge, line))
        self._highlight()

    def _highlight(self):
        selected = (self.drag.item, self.drag.edge) if self.drag else self._hover
        for item, edge, line in self.handles:
            chosen = selected is not None and item is selected[0] and edge == selected[1]
            line.set_linewidth(3 if chosen else 1.8)
            line.set_markersize(9 if chosen else 6)
            line.set_alpha(1 if chosen else 0.85)

    def hit(self, event, *, include_models=False):
        if event.inaxes is not self.app.axes or event.x is None:
            return None
        axis = self.app.axes
        candidates = []
        for item in reversed(self.items()):
            if not include_models and not self.editable(item):
                continue
            for edge in ("left", "right"):
                screen_x = axis.transData.transform((float(item[edge]), 0))[0]
                distance = abs(screen_x - event.x)
                if distance <= 9:
                    candidates.append((distance, item, edge))
        if not candidates:
            return None
        _, item, edge = min(candidates, key=lambda candidate: candidate[0])
        return item, edge

    def grids(self, comparison):
        if comparison is None:
            return [np.asarray(self.app.ppm)]
        offsets = comparison.get("offsets", {})
        return [
            np.asarray(record["ppm"]) + float(offsets.get(sid, offsets.get(str(sid), 0)))
            for sid in comparison.get("spectrum_ids", [])
            if (record := self.app.spectra.get(int(sid))) is not None
        ]

    def on_press(self, event):
        if not self.active or event.button != 1 or self.app._spectrum_dialog_open:
            return
        # A fitted domain often shares an edge with an ordinary area. Prefer
        # the editable handle rather than letting a newer readonly group hide it.
        hit = self.hit(event) or self.hit(event, include_models=True)
        if hit is None:
            return
        item, edge = hit
        if not self.editable(item):
            self.app.status.set(
                "Это интеграл Spintegrate: его площадь задаётся компонентами модели. "
                "Откройте S / двойной клик по интегралу и измените состав или область подгонки."
            )
            return
        comparison = self.app.comparison_record()
        grids = self.grids(comparison)
        if boundary_limits(grids, item["left"], item["right"], edge) is None:
            self.app.status.set("Границу нельзя перенести: нужно не менее трёх точек в каждом спектре.")
            return
        previous = (
            self.app.comparison_integral_state(comparison)
            if comparison is not None
            else self.app.capture_spectrum_state()
        )
        self.drag = _Drag(item, edge, previous, comparison, grids, self.context())
        self._set_cursor("sb_h_double_arrow")
        self.app.canvas.get_tk_widget().focus_set()
        self._highlight()
        self.app.canvas.draw_idle()

    def _move(self, event):
        drag = self.drag
        if drag is None or event.x is None or drag.context != self.context():
            return
        # Keep dragging outside the plot, clamping at the data extent instead of
        # dropping a drag when a narrow/zoomed edge is temporarily crossed.
        proposed = self.app.axes.transData.inverted().transform((event.x, event.y or 0))[0]
        moved = moved_boundary(
            drag.grids, drag.item["left"], drag.item["right"], drag.edge, proposed
        )
        if moved is None or moved == drag.item[drag.edge]:
            return
        drag.item[drag.edge] = moved
        for item, edge, line in self.handles:
            if item is drag.item and edge == drag.edge:
                line.set_xdata([moved, moved])
        self.app.status.set(
            f"I{drag.item.get('number', drag.item.get('id', '?'))}: "
            f"{drag.item['left']:.6f} … {drag.item['right']:.6f} ppm. "
            "Отпустите мышь — сохранить; Esc — отменить."
        )
        if self._pending is None:
            self._pending = self.app.after(85, self._preview)
        self.app.canvas.draw_idle()

    def on_motion(self, event):
        if not self.active:
            return
        if self.drag:
            self._move(event)
            return
        self._hover = self.hit(event)
        self._set_cursor("sb_h_double_arrow" if self._hover else "")
        self._highlight()
        self.app.canvas.draw_idle()

    def _stop_pending(self):
        if self._pending is not None:
            self.app.after_cancel(self._pending)
            self._pending = None

    def _preview(self):
        self._pending = None
        if self.drag and self.drag.context == self.context():
            self.app.draw()
            self.app.refresh_formula_workspace()

    def on_release(self, event):
        if self.drag is None or event.button not in (None, 1):
            return
        self._move(event)
        self._stop_pending()
        drag, self.drag = self.drag, None
        if drag.context != self.context():
            return
        action = "изменение границ интеграла"
        if drag.comparison is None:
            self.app.remember_spectrum_state(drag.previous, action)
        elif drag.previous != self.app.comparison_integral_state(drag.comparison):
            self.app._undo_stack.append(("comparison_integrals", action, drag.previous))
            self.app._redo_stack.clear()
            self.app.trim_history()
        self.app.draw()
        self.app.refresh_formula_workspace()
        self.app.status.set("Границы интеграла сохранены. Ctrl+Z — отменить; можно двигать следующую границу.")

    def cancel_drag(self):
        self._stop_pending()
        drag, self.drag = self.drag, None
        if drag is None:
            return False
        if drag.context == self.context():
            if drag.comparison is None:
                self.app.restore_spectrum_state(drag.previous)
            else:
                self.app.restore_comparison_integral_state(drag.previous)
            self.app.draw()
            self.app.refresh_formula_workspace()
        self._set_cursor("")
        return True

    def escape(self, _event=None):
        if not self.active:
            return None
        if self.cancel_drag():
            self.app.status.set("Перетаскивание отменено. Esc ещё раз — выйти из изменения границ.")
        else:
            self.set_active(False)
            self.app.status.set("Изменение границ интегралов выключено.")
        return "break"

    def on_leave(self, _event):
        if not self.drag:
            self._hover = None
            self._set_cursor("")
            self._highlight()
