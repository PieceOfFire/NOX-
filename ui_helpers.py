"""Small shared Tk helpers without application-specific state."""

import tkinter as tk
from ui_theme import BORDER, SURFACE, TEXT


def add_tooltip(widget, text):
    tip = None

    def show(_event=None):
        nonlocal tip
        if tip is not None:
            return
        tip = tk.Toplevel(widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(
            f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + widget.winfo_height() + 3}"
        )
        tk.Label(
            tip,
            text=text,
            bg=SURFACE,
            fg=TEXT,
            padx=6,
            pady=3,
            highlightthickness=1,
            highlightbackground=BORDER,
        ).pack()

    def hide(_event=None):
        nonlocal tip
        if tip is not None:
            tip.destroy()
            tip = None

    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<Destroy>", hide, add="+")
