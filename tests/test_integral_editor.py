"""Sample-safe bounds, including unlike grids and comparison X offsets."""

import numpy as np
import pytest

from integral_editor import boundary_limits, moved_boundary


def test_left_edge_keeps_three_samples_and_never_inverts():
    grid = np.arange(11.0)[::-1]
    assert boundary_limits([grid], 2.5, 7.2, "left") == (0, 5)
    assert moved_boundary([grid], 2.5, 7.2, "left", 20) == 5
    assert moved_boundary([grid], 2.5, 7.2, "left", -2) == 0
    assert moved_boundary([grid], 2.5, 7.2, "left", 3.123456) == 3.123456


def test_right_edge_keeps_three_samples_in_all_displayed_grids():
    first = np.arange(11.0)
    second = np.arange(11.0) + 0.25
    assert boundary_limits([first, second], 2.1, 7, "right") == (5, 10)
    moved = moved_boundary([first, second], 2.1, 7, "right", -100)
    assert moved == 5
    assert all(np.count_nonzero((x >= 2.1) & (x <= moved)) >= 3 for x in [first, second])


def test_unavailable_or_invalid_bounds_rejected():
    assert boundary_limits([], 1, 2, "left") is None
    assert boundary_limits([[1, 2]], 1, 2, "left") is None
    assert boundary_limits([np.arange(5)], 1, 1, "left") is None
    assert boundary_limits([np.arange(5), np.arange(10, 15)], 1, 12, "right") is None
    assert moved_boundary([np.arange(10)], 2, 7, "left", np.nan) is None
    with pytest.raises(ValueError):
        boundary_limits([np.arange(5)], 1, 4, "center")


def test_handles_nonfinite_grid_and_leaves_original_grid_unchanged():
    grid = np.array([4, 3, np.nan, 2, 1, np.inf])
    original = grid.copy()
    assert boundary_limits([grid], 1, 4, "left") == (1, 2)
    np.testing.assert_array_equal(original, grid)


def test_narrow_sampling_does_not_round_away_sub_centippm_changes():
    grid = np.linspace(5.01, 5, 1001)
    moved = moved_boundary([grid], 5.001, 5.009, "left", 5.00112345)
    assert moved == 5.00112345
