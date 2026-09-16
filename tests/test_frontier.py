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


# --- clusters and the best frontier (ADR-027) ------------------------------

from robot_skills.explore_skill import find_best_frontier, find_frontier_clusters  # noqa: E402


def _room_grid():
    """12x7 grid: a free band in the middle rows, unknown elsewhere.

    Row 3 is free across cols 1-10 (a long frontier: unknown above and below).
    A second free speck at (row 1, col 1-2) touches only a little unknown.
    """
    width, height = 12, 7
    data = [UNKNOWN] * (width * height)
    for col in range(1, 11):
        data[3 * width + col] = FREE
    return make_grid(width, height, data)


def test_frontier_cells_are_grouped_into_connected_clusters():
    grid = make_grid(9, 5, [UNKNOWN] * 45)
    for col in (1, 2, 3):          # cluster A
        grid.data[2 * 9 + col] = FREE
    for col in (5, 6, 7):          # cluster B, separated by one unknown column
        grid.data[2 * 9 + col] = FREE
    clusters = find_frontier_clusters(grid, min_cells=1)
    assert sorted(c.size for c in clusters) == [3, 3]


def test_tiny_clusters_are_dropped_as_noise():
    grid = make_grid(5, 5, [UNKNOWN] * 25)
    grid.data[2 * 5 + 2] = FREE
    assert find_frontier_clusters(grid, min_cells=4) == []


def test_a_cluster_target_is_a_member_cell_near_its_centre():
    (cluster,) = find_frontier_clusters(_room_grid())
    tx, ty = cluster.target
    assert cluster.size == 10
    assert 5.0 <= tx <= 7.0 and ty == 3.5


def test_a_long_frontier_a_bit_further_beats_a_short_one_next_to_the_robot():
    """The failure measured live: the nearest rule crept along one wall.

    At the real map resolution (0.05 m): a 20 cm speck of unknown beside the
    robot versus a 3 m unexplored edge five metres away.
    """
    width, height, res = 80, 100, 0.05
    data = [UNKNOWN] * (width * height)
    for col in range(10, 14):                  # 0.2 m speck near the robot
        data[10 * width + col] = FREE
    for col in range(10, 70):                  # 3 m edge further away
        data[90 * width + col] = FREE
    grid = make_grid(width, height, data, resolution=res)
    robot = (0.6, -0.5)
    assert find_nearest_frontier(grid, *robot)[1] < 1.0        # old rule: the speck
    assert find_best_frontier(grid, *robot)[1] > 4.0           # new rule: the long edge


def test_best_frontier_respects_exclusions_min_distance_and_bounds():
    grid = _room_grid()
    (cluster,) = find_frontier_clusters(grid)
    assert find_best_frontier(grid, 0.0, 0.0, excluded=[cluster.target]) is None
    assert find_best_frontier(grid, *cluster.target, min_distance=0.6) is None
    assert find_best_frontier(grid, 0.0, 0.0, bounds=(100.0, 100.0, 101.0, 101.0)) is None


def test_no_frontier_left_means_none():
    assert find_best_frontier(make_grid(3, 3, [FREE] * 9), 0.0, 0.0) is None


def test_a_cluster_target_keeps_clear_of_walls():
    """Regression for the live failure: a frontier target against a wall sits in
    Nav2's inflated cost and fails to plan. The clearest member is chosen."""
    width, height, res = 60, 40, 0.05
    data = [UNKNOWN] * (width * height)
    for col in range(5, 55):                       # a 2.5 m frontier edge at row 20
        data[20 * width + col] = FREE
    for col in range(5, 35):                       # a wall right beside its left part
        data[21 * width + col] = OCCUPIED
    (cluster,) = find_frontier_clusters(make_grid(width, height, data, resolution=res))
    target_col = int(cluster.target[0] / res)
    assert target_col >= 35 + 10, f'target at col {target_col} is inside the wall\'s 0.5 m band'
