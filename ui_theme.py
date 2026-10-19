"""Shared light desktop styling, based on the spectrum-processing interface."""

import tkinter as tk
from tkinter import font, ttk

PAGE = "#f4f5f7"
SURFACE = "#ffffff"
TEXT = "#202936"
MUTED = "#617084"
BORDER = "#d1d8e2"
GRID = "#e9edf2"
SELECTED = "#2468b4"
ACTIVE = "#e7edf4"
# Controls need their own surface/border; white-on-near-white looked like labels.
BUTTON_SURFACE = "#dce5f0"
BUTTON_BORDER = "#76879e"
BUTTON_HOVER = "#c7d8ed"
BUTTON_PRESSED = "#adc6e3"
ERROR = "#ad3038"
WARNING = "#855b0b"
SUCCESS = "#287044"
PRIMARY_ACTIVE = "#1b5696"
DISABLED = "#737e8c"


def status_palette(level="info"):
    """Low-contrast status surfaces; the text, not colour alone, conveys meaning."""
    return dict(
        {
            "muted": {"bg": PAGE, "fg": MUTED, "border": BORDER},
            "info": {"bg": "#edf4fc", "fg": "#245b95", "border": "#cedef0"},
            "success": {"bg": "#edf7f0", "fg": SUCCESS, "border": "#cde4d4"},
            "warning": {"bg": "#fff7e8", "fg": WARNING, "border": "#ead9b2"},
            "error": {"bg": "#fdf0f1", "fg": ERROR, "border": "#edced1"},
        }.get(level, {"bg": PAGE, "fg": MUTED, "border": BORDER})
    )


def install_theme(root):
    """Apply once, before constructing widgets; later dialogs inherit the same defaults."""
    root.configure(background=PAGE)
    if root.__dict__.get("_app_theme_installed"):
        return
    root._app_theme_installed = True
    root.option_add("*Font", "TkDefaultFont")
    for widget_class in (
        "Frame",
        "LabelFrame",
        "Toplevel",
        "Label",
        "Checkbutton",
        "Radiobutton",
        "Scale",
        "Panedwindow",
    ):
        root.option_add(f"*{widget_class}.background", PAGE)
    for widget_class in ("Label", "LabelFrame", "Button", "Checkbutton", "Radiobutton", "Scale"):
        root.option_add(f"*{widget_class}.foreground", TEXT)
    for widget_class in ("Button", "Menubutton"):
        for option, value in (
            ("background", BUTTON_SURFACE),
            ("foreground", TEXT),
            ("relief", "raised"),
            ("borderWidth", 1),
            ("highlightThickness", 1),
            ("highlightBackground", BUTTON_BORDER),
            ("highlightColor", SELECTED),
            ("padX", 8),
            ("padY", 4),
            ("activeBackground", BUTTON_HOVER),
            ("activeForeground", TEXT),
            ("disabledForeground", DISABLED),
            ("takeFocus", 1),
        ):
            root.option_add(f"*{widget_class}.{option}", value)
    root.option_add("*LabelFrame.borderWidth", 1)
    root.option_add("*LabelFrame.relief", "flat")
    for widget_class in ("Entry", "Spinbox"):
        root.option_add(f"*{widget_class}.background", SURFACE)
        root.option_add(f"*{widget_class}.foreground", TEXT)
        root.option_add(f"*{widget_class}.relief", "flat")
        root.option_add(f"*{widget_class}.borderWidth", 1)
        root.option_add(f"*{widget_class}.highlightThickness", 1)
        root.option_add(f"*{widget_class}.highlightBackground", BORDER)
        root.option_add(f"*{widget_class}.highlightColor", SELECTED)
        root.option_add(f"*{widget_class}.disabledBackground", PAGE)
        root.option_add(f"*{widget_class}.disabledForeground", DISABLED)
        root.option_add(f"*{widget_class}.selectBackground", SELECTED)
        root.option_add(f"*{widget_class}.selectForeground", SURFACE)
    root.option_add("*Spinbox.buttonBackground", PAGE)
    root.option_add("*Spinbox.readonlyBackground", SURFACE)
    root.option_add("*Scrollbar.background", ACTIVE)
    root.option_add("*Scrollbar.activeBackground", BORDER)
    root.option_add("*Scrollbar.troughColor", PAGE)
    root.option_add("*Scrollbar.relief", "flat")
    root.option_add("*Scrollbar.borderWidth", 0)
    root.option_add("*Scrollbar.highlightThickness", 0)
    root.option_add("*Scrollbar.width", 12)
    root.option_add("*Canvas.background", SURFACE)
    root.option_add("*Text.background", SURFACE)
    root.option_add("*Text.foreground", TEXT)
    root.option_add("*Text.relief", "flat")
    root.option_add("*Text.highlightThickness", 1)
    root.option_add("*Text.highlightBackground", BORDER)
    root.option_add("*Text.highlightColor", SELECTED)
    root.option_add("*Listbox.background", SURFACE)
    root.option_add("*Listbox.foreground", TEXT)
    root.option_add("*Listbox.relief", "flat")
    root.option_add("*Listbox.borderWidth", 0)
    root.option_add("*Listbox.highlightThickness", 1)
    root.option_add("*Listbox.highlightBackground", BORDER)
    root.option_add("*Listbox.highlightColor", SELECTED)
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
    # Clam is bundled with Tk on supported platforms and honours border/field
    # colours consistently. Native file/folder dialogs remain native dialogs.
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure(".", font="TkDefaultFont")
    style.configure("TFrame", background=PAGE)
    style.configure("TLabel", background=PAGE, foreground=TEXT)
    style.configure("TLabelframe", background=PAGE)
    style.configure("TLabelframe.Label", background=PAGE, foreground=TEXT)
    style.configure("TCheckbutton", background=PAGE, foreground=TEXT)
    style.configure("TRadiobutton", background=PAGE, foreground=TEXT)
    for kind in ("TCheckbutton", "TRadiobutton"):
        style.map(
            kind,
            background=[("active", PAGE)],
            foreground=[("disabled", DISABLED)],
            indicatorbackground=[("disabled", PAGE), ("!disabled", SURFACE)],
        )
    style.configure(
        "TButton",
        background=BUTTON_SURFACE,
        foreground=TEXT,
        padding=(8, 4),
        relief="raised",
        borderwidth=1,
        bordercolor=BUTTON_BORDER,
        lightcolor=BUTTON_SURFACE,
        darkcolor=BUTTON_SURFACE,
        focuscolor=SELECTED,
    )
    style.map(
        "TButton",
        background=[("disabled", PAGE), ("pressed", BUTTON_PRESSED), ("active", BUTTON_HOVER)],
        foreground=[("disabled", DISABLED)],
        relief=[("pressed", "sunken"), ("!pressed", "raised")],
        bordercolor=[
            ("disabled", BORDER),
            ("focus", SELECTED),
            ("pressed", SELECTED),
            ("!disabled", BUTTON_BORDER),
        ],
        lightcolor=[("disabled", PAGE), ("pressed", BUTTON_PRESSED), ("active", BUTTON_HOVER)],
        darkcolor=[("disabled", PAGE), ("pressed", BUTTON_PRESSED), ("active", BUTTON_HOVER)],
    )
    # Dropdown command buttons (e.g. View) have the same visible outline.
    style.configure(
        "TMenubutton",
        background=BUTTON_SURFACE,
        foreground=TEXT,
        bordercolor=BUTTON_BORDER,
        lightcolor=BUTTON_SURFACE,
        darkcolor=BUTTON_SURFACE,
        borderwidth=1,
        relief="raised",
        padding=(8, 4),
        arrowcolor=TEXT,
    )
    style.map(
        "TMenubutton",
        background=[("disabled", PAGE), ("pressed", BUTTON_PRESSED), ("active", BUTTON_HOVER)],
        foreground=[("disabled", DISABLED)],
        bordercolor=[("focus", SELECTED), ("!disabled", BUTTON_BORDER)],
    )
    style.configure(
        "Primary.TButton",
        background=SELECTED,
        foreground=SURFACE,
        bordercolor=SELECTED,
        lightcolor=SELECTED,
        darkcolor=SELECTED,
        focuscolor=SURFACE,
    )
    style.map(
        "Primary.TButton",
        background=[("disabled", PAGE), ("pressed", PRIMARY_ACTIVE), ("active", PRIMARY_ACTIVE)],
        foreground=[("disabled", DISABLED), ("!disabled", SURFACE)],
        bordercolor=[("disabled", BORDER), ("!disabled", SELECTED)],
        lightcolor=[("disabled", PAGE), ("!disabled", SELECTED)],
        darkcolor=[("disabled", PAGE), ("!disabled", SELECTED)],
    )
    for kind in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(
            kind,
            fieldbackground=SURFACE,
            foreground=TEXT,
            background=SURFACE,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            borderwidth=1,
            padding=(5, 3),
            selectbackground=SELECTED,
            selectforeground=SURFACE,
            arrowcolor=MUTED,
        )
        style.map(
            kind,
            fieldbackground=[("disabled", PAGE), ("readonly", SURFACE)],
            foreground=[("disabled", DISABLED), ("readonly", TEXT)],
            bordercolor=[("focus", SELECTED), ("!focus", BORDER)],
            lightcolor=[("focus", SELECTED), ("!focus", BORDER)],
            darkcolor=[("focus", SELECTED), ("!focus", BORDER)],
            background=[("active", ACTIVE), ("!disabled", SURFACE)],
            arrowcolor=[("disabled", DISABLED), ("!disabled", MUTED)],
        )
    style.configure("TNotebook", background=PAGE, borderwidth=0, tabmargins=(0, 3, 0, 0))
    style.configure(
        "TNotebook.Tab",
        background=PAGE,
        foreground=MUTED,
        padding=(12, 6),
        borderwidth=1,
        bordercolor=BORDER,
        lightcolor=PAGE,
        darkcolor=PAGE,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE), ("active", ACTIVE)],
        foreground=[("disabled", DISABLED), ("selected", TEXT), ("active", TEXT)],
        lightcolor=[("selected", SURFACE)],
        darkcolor=[("selected", SURFACE)],
    )
    style.configure("TSeparator", background=BORDER)
    style.configure(
        "Vertical.TScrollbar",
        background=ACTIVE,
        troughcolor=PAGE,
        bordercolor=PAGE,
        lightcolor=ACTIVE,
        darkcolor=ACTIVE,
        arrowcolor=MUTED,
        borderwidth=0,
        arrowsize=12,
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=ACTIVE,
        troughcolor=PAGE,
        bordercolor=PAGE,
        lightcolor=ACTIVE,
        darkcolor=ACTIVE,
        arrowcolor=MUTED,
        borderwidth=0,
        arrowsize=12,
    )
    style.configure("TProgressbar", background=SELECTED, troughcolor=ACTIVE, borderwidth=0)
    style.configure(
        "Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        font="TkDefaultFont",
        rowheight=max(26, font.nametofont("TkDefaultFont", root=root).metrics("linespace") + 8),
        borderwidth=1,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        relief="flat",
    )
    style.configure(
        "Treeview.Heading",
        font="AppHeadingFont",
        padding=(6, 6),
        background=PAGE,
        foreground=TEXT,
        relief="flat",
        borderwidth=0,
    )
    style.map("Treeview.Heading", background=[("active", ACTIVE)])
    style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", SURFACE)])


def button(parent, text, command, *, compact=False, **kwargs):
    options = dict(
        font="TkDefaultFont",
        background=BUTTON_SURFACE,
        foreground=TEXT,
        activebackground=BUTTON_HOVER,
        activeforeground=TEXT,
        relief="raised",
        borderwidth=1,
        highlightthickness=1,
        highlightbackground=BUTTON_BORDER,
        highlightcolor=SELECTED,
        padx=5 if compact else 8,
        pady=3,
    )
    options.update(kwargs)
    return tk.Button(parent, text=text, command=command, **options)


def mark_primary(widget, enabled=True):
    """Emphasise one main action without changing its command, state or layout."""
    if isinstance(widget, ttk.Button):
        widget.configure(style="Primary.TButton" if enabled else "TButton")
    else:
        widget.configure(
            background=SELECTED if enabled else BUTTON_SURFACE,
            foreground=SURFACE if enabled else TEXT,
            activebackground=PRIMARY_ACTIVE if enabled else BUTTON_HOVER,
            activeforeground=SURFACE if enabled else TEXT,
            disabledforeground="#dce6f2" if enabled else DISABLED,
            highlightbackground=SELECTED if enabled else BUTTON_BORDER,
            highlightcolor=TEXT if enabled else SELECTED,
        )
    return widget


def primary_button(parent, text, command, *, compact=False, **kwargs):
    return mark_primary(button(parent, text, command, compact=compact, **kwargs))


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
