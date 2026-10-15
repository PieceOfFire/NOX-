"""Canvas viewport with stable document coordinates at every zoom level."""

from __future__ import annotations
import tkinter as tk


class FormulaCanvas(tk.Canvas):
    def __init__(self, *args, **kwargs):
        self.zoom = 1.0
        self.world_region = tuple(kwargs.get("scrollregion", (0, 0, 1800, 1200)))
        super().__init__(*args, **kwargs)

    def _points(self, coords):
        values = []
        for value in coords:
            if isinstance(value, (tuple, list)):
                values.extend(self._points(value))
            else:
                values.append(float(value) * self.zoom)
        return values

    def _create(self, itemType, args, kw):
        opts = dict(kw)
        if isinstance(opts.get("font"), (tuple, list)) and len(opts["font"]) > 1:
            font = list(opts["font"])
            font[1] = max(5, round(float(font[1]) * self.zoom))
            opts["font"] = tuple(font)
        if isinstance(opts.get("width"), (float, int)):
            opts["width"] = max(0.5, float(opts["width"]) * self.zoom)
        if isinstance(opts.get("dash"), (tuple, list)):
            opts["dash"] = tuple(max(1, round(v * self.zoom)) for v in opts["dash"])
        if isinstance(opts.get("arrowshape"), (tuple, list)):
            opts["arrowshape"] = tuple(v * self.zoom for v in opts["arrowshape"])
        return super()._create(itemType, self._points(args), opts)

    def canvasx(self, x, gridspacing=None):
        return super().canvasx(x, gridspacing) / self.zoom

    def canvasy(self, y, gridspacing=None):
        return super().canvasy(y, gridspacing) / self.zoom

    def coords(self, tagOrId, *args):
        if args:
            return super().coords(tagOrId, *self._points(args))
        return [v / self.zoom for v in super().coords(tagOrId)]

    def move(self, tagOrId, xAmount, yAmount):
        return super().move(tagOrId, xAmount * self.zoom, yAmount * self.zoom)

    def configure(self, cnf=None, **kw):
        options = dict(cnf or {})
        options.update(kw)
        if "scrollregion" in options:
            self.world_region = tuple(options["scrollregion"])
            options["scrollregion"] = self._points(self.world_region)
        return super().configure(options)

    config = configure

    def center_at(self, x, y):
        left, top, right, bottom = self.world_region
        self.xview_moveto((x - left - self.winfo_width() / (2 * self.zoom)) / max(1, right - left))
        self.yview_moveto((y - top - self.winfo_height() / (2 * self.zoom)) / max(1, bottom - top))
