"""Scrollable parameter pages without global mouse-wheel bindings."""

import tkinter as tk
from tkinter import ttk
from ui_theme import PAGE


class ParameterPage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=PAGE, width=360, height=250)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.body = ttk.Frame(self.canvas, padding=10)
        self.window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind(
            "<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width)
        )
        self.remainder = 0

    def wheel(self, event):
        if getattr(event, "num", None) in (4, 5):
            steps = -1 if event.num == 4 else 1
        else:
            self.remainder += event.delta
            steps = -int(self.remainder / 120)
            self.remainder += steps * 120
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(steps, "units")
        return "break"

    def bind_scrolling(self):
        def visit(widget):
            # Numeric entries retain their fine/coarse wheel adjustment.
            if widget.winfo_class() != "TEntry":
                for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    widget.bind(event, self.wheel)
            for child in widget.winfo_children():
                visit(child)

        visit(self)
