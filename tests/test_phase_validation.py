"""Displayed phase values must be numeric and finite before committing."""

import math

import pytest

from nmr_dialogs import parse_phase_value


@pytest.mark.parametrize(
    "value", ["", "-", ".", "wrong", "NaN", "nan", "inf", "-Infinity", "1e9999", None]
)
def test_invalid_displayed_phase(value):
    with pytest.raises(ValueError):
        parse_phase_value(value)


@pytest.mark.parametrize(
    "text, expected", [(" 2.5 ", 2.5), ("-0,125", -0.125), ("1e2", 100), (12.0, 12)]
)
def test_finite_displayed_phase(text, expected):
    value = parse_phase_value(text)
    assert math.isfinite(value)
    assert value == expected
