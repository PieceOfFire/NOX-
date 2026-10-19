"""Theme tokens and primary actions remain usable without opening desktop windows."""

from unittest.mock import Mock

import ui_theme


def test_status_palettes_are_independent_and_have_all_surface_tokens():
    for level in ("muted", "info", "success", "warning", "error", "unknown"):
        palette = ui_theme.status_palette(level)
        assert set(palette) == {"bg", "fg", "border"}
        assert all(value.startswith("#") and len(value) == 7 for value in palette.values())
    changed = ui_theme.status_palette("warning")
    changed["fg"] = "changed"
    assert ui_theme.status_palette("warning")["fg"] == ui_theme.WARNING
    assert ui_theme.status_palette("unknown") == ui_theme.status_palette("muted")


def test_mark_primary_only_changes_visual_options():
    widget = Mock()
    assert ui_theme.mark_primary(widget) is widget
    enabled = widget.configure.call_args.kwargs
    assert enabled["background"] == ui_theme.SELECTED
    assert enabled["foreground"] == ui_theme.SURFACE
    assert not {"state", "command", "relief"}.intersection(enabled)
    ui_theme.mark_primary(widget, False)
    assert widget.configure.call_args.kwargs["background"] == ui_theme.BUTTON_SURFACE


def test_button_surfaces_and_edges_are_distinct_from_panel():
    def luminance(color):
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * w for c, w in zip(linear, (0.2126, 0.7152, 0.0722)))

    def contrast(a, b):
        light, dark = sorted((luminance(a), luminance(b)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    assert (
        len(
            {ui_theme.PAGE, ui_theme.BUTTON_SURFACE, ui_theme.BUTTON_HOVER, ui_theme.BUTTON_PRESSED}
        )
        == 4
    )
    assert contrast(ui_theme.BUTTON_BORDER, ui_theme.PAGE) >= 3
    assert contrast(ui_theme.BUTTON_BORDER, ui_theme.BUTTON_SURFACE) >= 2.5
    assert contrast(ui_theme.TEXT, ui_theme.BUTTON_SURFACE) >= 7
