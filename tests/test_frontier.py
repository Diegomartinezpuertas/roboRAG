"""Tests for frontier selection over a synthetic occupancy grid."""

from types import SimpleNamespace

from robot_skills.explore_skill import find_nearest_frontier

FREE, UNKNOWN, OCCUPIED = 0, -1, 100


def make_grid(width, height, data, resolution=1.0, ox=0.0, oy=0.0):
    """Builds a minimal duck-typed OccupancyGrid for the frontier finder."""
    return SimpleNamespace(
        info=SimpleNamespace(
            width=width, height=height, resolution=resolution,
            origin=SimpleNamespace(position=SimpleNamespace(x=ox, y=oy)),
        ),
        data=data,
    )


def _strip_grid():
    """5x5 grid: all unknown except a free strip at row 2, cols 1-3.

    The free cells border unknown space, so each is a frontier. Cell centers
    are at (col+0.5, row+0.5) with resolution 1.0.
    """
    data = [UNKNOWN] * 25
    for col in (1, 2, 3):
        data[2 * 5 + col] = FREE
    return make_grid(5, 5, data)


def test_picks_nearest_frontier_to_robot():
    grid = _strip_grid()
    # Robot at origin: nearest free-with-unknown-neighbor is col 1 -> center (1.5, 2.5)
    assert find_nearest_frontier(grid, 0.0, 0.0) == (1.5, 2.5)


def test_min_distance_skips_frontier_at_robot():
    grid = _strip_grid()
    # Robot sitting essentially on the (1.5, 2.5) frontier: it must be skipped
    # (navigating to your own pose never expands the map), so a farther one wins.
    result = find_nearest_frontier(grid, 1.5, 2.5, min_distance=0.6)
    assert result is not None
    assert result != (1.5, 2.5)


def test_bounds_restrict_search():
    grid = _strip_grid()
    # Restrict to a zone that only contains the col-3 frontier (x in [3.0, 4.0]).
    result = find_nearest_frontier(grid, 0.0, 0.0, bounds=(3.0, 2.0, 4.0, 3.0))
    assert result == (3.5, 2.5)


def test_excluded_frontiers_skipped():
    grid = _strip_grid()
    result = find_nearest_frontier(grid, 0.0, 0.0, excluded=[(1.5, 2.5)])
    assert result == (2.5, 2.5)


def test_no_frontier_returns_none():
    # Entirely unknown: no free cells at all -> no frontier.
    grid = make_grid(5, 5, [UNKNOWN] * 25)
    assert find_nearest_frontier(grid, 0.0, 0.0) is None
