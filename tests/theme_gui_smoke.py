"""Hidden desktop check for shared styles, focus affordances and tooltip cleanup."""

import tkinter as tk
from tkinter import ttk

from gui_smoke import make_processed  # noqa: F401 -- also installs the project import path
from ui_helpers import add_tooltip, fit_combobox_popup
from ui_theme import (
    BUTTON_SURFACE,
    BUTTON_BORDER,
    BUTTON_HOVER,
    BUTTON_PRESSED,
    PAGE,
    SELECTED,
    SURFACE,
    install_theme,
    mark_primary,
    primary_button,
)


def main():
    app = tk.Tk()
    app.attributes("-alpha", 0)
    errors = []
    app.report_callback_exception = lambda kind, value, tb: errors.append(str(value))
    try:
        install_theme(app)
        install_theme(app)
        ordinary = tk.Button(app, text="Secondary action")
        ordinary.pack()
        menu_button = tk.Menubutton(app, text="View")
        menu_button.pack()
        primary = primary_button(app, "Apply", lambda: None)
        primary.pack()
        themed = ttk.Button(app, text="Apply")
        mark_primary(themed).pack()
        box = ttk.Combobox(app, state="readonly", values=("Short", "A much longer method name"))
        box.pack()
        entry = tk.Entry(app)
        entry.pack()
        tabs = ttk.Notebook(app)
        pane = ttk.Frame(tabs)
        tabs.add(pane, text="Parameters")
        tabs.pack()
        table = ttk.Treeview(pane, columns=("name",), show="headings", height=2)
        table.heading("name", text="Spectrum")
        table.insert("", "end", values=("11",))
        table.pack()
        app.update()
        assert ordinary.cget("relief") == "raised"
        assert ordinary.cget("background") == BUTTON_SURFACE
        assert ordinary.cget("highlightbackground") == BUTTON_BORDER
        assert ordinary.cget("activebackground") == BUTTON_HOVER
        assert menu_button.cget("background") == BUTTON_SURFACE
        assert ordinary.cget("highlightcolor") == SELECTED
        assert primary.cget("background") == SELECTED
        assert themed.cget("style") == "Primary.TButton"
        assert entry.cget("highlightcolor") == SELECTED
        style = ttk.Style(app)
        assert style.lookup("TButton", "background") == BUTTON_SURFACE
        assert style.lookup("TButton", "bordercolor") == BUTTON_BORDER
        assert style.lookup("TButton", "background", ("active",)) == BUTTON_HOVER
        assert style.lookup("TButton", "background", ("pressed",)) == BUTTON_PRESSED
        assert style.lookup("TButton", "relief", ("pressed",)) == "sunken"
        assert style.lookup("TEntry", "fieldbackground") == SURFACE
        assert style.lookup("TButton", "background", ("disabled",)) == PAGE
        assert style.lookup("Primary.TButton", "foreground", ("disabled",)) != SURFACE
        assert style.lookup("TCombobox", "fieldbackground", ("readonly",)) == SURFACE
        ordinary.configure(relief="sunken")
        assert ordinary.cget("relief") == "sunken"  # Integration/tool toggles still work.
        mark_primary(primary, False)
        assert primary.cget("background") == BUTTON_SURFACE
        fit_combobox_popup(box)
        add_tooltip(ordinary, lambda: "Live status explanation", delay=0)
        ordinary.event_generate("<Enter>")
        app.update()
        assert any(isinstance(child, tk.Toplevel) for child in ordinary.winfo_children())
        ordinary.event_generate("<Leave>")
        app.update()
        assert not ordinary.winfo_children()
        ordinary.event_generate("<Enter>")
        ordinary.destroy()
        app.update()
        assert not errors, errors
        print("Theme GUI smoke passed")
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
