"""Small shared Tk helpers without application-specific state."""

import tkinter as tk
from tkinter import font
from ui_theme import BORDER, SURFACE, TEXT


def fit_combobox_popup(widget):
    """Allow full method names in the native popdown without widening its panel."""
    values = widget.cget("values")
    metrics = font.Font(root=widget, font=widget.cget("font") or "TkDefaultFont")
    width = max((metrics.measure(value) for value in values), default=0) + 36
    width = min(max(widget.winfo_width(), width), widget.winfo_screenwidth() - 40)
    popup = widget.tk.call("ttk::combobox::PopdownWindow", str(widget))
    widget.tk.call("wm", "minsize", popup, width, 1)


def add_tooltip(widget, text, *, delay=450):
    """A quiet, delayed hint; ``text`` may be a callable for live status details."""
    tip = None
    pending = None

    def show(_event=None):
        nonlocal tip
        nonlocal pending
        pending = None
        if tip is not None or not widget.winfo_exists():
            return
        value = text() if callable(text) else text
        if not value:
            return
        tip = tk.Toplevel(widget)
        tip.withdraw()
        tip.wm_overrideredirect(True)
        tk.Label(
            tip,
            text=value,
            bg=SURFACE,
            fg=TEXT,
            padx=9,
            pady=6,
            justify="left",
            wraplength=min(400, max(180, widget.winfo_screenwidth() - 40)),
            highlightthickness=1,
            highlightbackground=BORDER,
        ).pack()
        tip.update_idletasks()
        width, height = tip.winfo_reqwidth(), tip.winfo_reqheight()
        margin = 8
        x = max(margin, min(widget.winfo_rootx(), widget.winfo_screenwidth() - width - margin))
        y = widget.winfo_rooty() + widget.winfo_height() + 5
        if y + height + margin > widget.winfo_screenheight():
            y = max(margin, widget.winfo_rooty() - height - 5)
        tip.wm_geometry(f"+{x}+{y}")
        tip.deiconify()

    def schedule(_event=None):
        nonlocal pending
        hide()
        pending = widget.after(delay, show)

    def hide(_event=None):
        nonlocal tip, pending
        if pending is not None:
            try:
                widget.after_cancel(pending)
            except tk.TclError:
                pass
            pending = None
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                pass
            tip = None

    widget.bind("<Enter>", schedule, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<ButtonPress>", hide, add="+")
    widget.bind("<FocusOut>", hide, add="+")
    widget.bind("<Escape>", hide, add="+")
    widget.bind("<Destroy>", hide, add="+")
