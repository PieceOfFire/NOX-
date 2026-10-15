"""Shared light desktop styling, based on the spectrum-processing interface."""

import tkinter as tk
from tkinter import font, ttk

PAGE = "#f0f0f0"
SURFACE = "#ffffff"
TEXT = "#202020"
MUTED = "#666666"
BORDER = "#b7b7b7"
GRID = "#ececec"
SELECTED = "#0078d7"
ACTIVE = "#e5e5e5"
ERROR = "#a4262c"
WARNING = "#8a5a00"
SUCCESS = "#28733f"


def install_theme(root):
    """Apply once, before constructing widgets; later dialogs inherit the same defaults."""
    root.configure(background=PAGE)
    root.option_add("*Font", "TkDefaultFont")
    for widget_class in (
        "Frame",
        "LabelFrame",
        "Toplevel",
        "Label",
        "Button",
        "Checkbutton",
        "Radiobutton",
        "Scale",
        "Panedwindow",
    ):
        root.option_add(f"*{widget_class}.background", PAGE)
    for widget_class in ("Label", "LabelFrame", "Button", "Checkbutton", "Radiobutton", "Scale"):
        root.option_add(f"*{widget_class}.foreground", TEXT)
    root.option_add("*Button.borderWidth", 1)
    root.option_add("*Button.padX", 6)
    root.option_add("*Button.padY", 3)
    root.option_add("*Button.activeBackground", ACTIVE)
    root.option_add("*Canvas.background", SURFACE)
    root.option_add("*Text.background", SURFACE)
    root.option_add("*Text.foreground", TEXT)
    root.option_add("*Listbox.background", SURFACE)
    root.option_add("*Listbox.selectBackground", SELECTED)
    root.option_add("*Listbox.selectForeground", SURFACE)
    root.option_add("*TCombobox*Listbox.font", "TkDefaultFont")
    root._app_fonts = {}
    base = font.nametofont("TkDefaultFont", root=root).actual()
    for name, size, weight in (
        ("AppHeadingFont", 10, "bold"),
        ("AppTitleFont", 11, "bold"),
        ("AppSmallFont", 8, "normal"),
        ("AppValueFont", 12, "bold"),
        ("AppPadFont", 12, "normal"),
    ):
        root._app_fonts[name] = font.Font(
            root=root, name=name, **{**base, "size": size, "weight": weight}
        )
    style = ttk.Style(root)
    style.configure(".", font="TkDefaultFont")
    style.configure("TFrame", background=PAGE)
    style.configure("TLabel", background=PAGE, foreground=TEXT)
    style.configure("TLabelframe", background=PAGE)
    style.configure("TLabelframe.Label", background=PAGE, foreground=TEXT)
    style.configure("TCheckbutton", background=PAGE, foreground=TEXT)
    style.configure("TRadiobutton", background=PAGE, foreground=TEXT)
    style.configure(
        "Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        font="TkDefaultFont",
        rowheight=max(22, font.nametofont("TkDefaultFont").metrics("linespace") + 6),
    )
    style.configure("Treeview.Heading", font="TkDefaultFont")
    style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", SURFACE)])


def button(parent, text, command, *, compact=False):
    return tk.Button(
        parent,
        text=text,
        command=command,
        font="TkDefaultFont",
        bg=PAGE,
        fg=TEXT,
        activebackground=ACTIVE,
        activeforeground=TEXT,
        relief="raised",
        bd=1,
        padx=5 if compact else 8,
        pady=3,
    )


def formula_palette():
    # Node colours encode operations/integrals; window chrome follows the spectrum tools.
    return dict(
        ink=TEXT,
        muted=MUTED,
        page=PAGE,
        panel=PAGE,
        toolbar=PAGE,
        line=BORDER,
        primary=SELECTED,
        primary_dark="#185fa5",
        green="#2e9d67",
        green_dark="#197a4d",
        violet="#7657e8",
        amber="#f59e0b",
        danger=ERROR,
        header=PAGE,
    )
