"""Basic frontier exploration: finds unexplored edges in the occupancy grid."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only a type hint; guarding it keeps this pure-logic module importable
    # (and unit-testable) without a ROS environment.
    from nav_msgs.msg import OccupancyGrid

UNKNOWN = -1


def find_nearest_frontier(
    grid: OccupancyGrid,
    robot_x: float,
    robot_y: float,
    min_distance: float = 0.6,
    excluded: list[tuple[float, float]] | None = None,
    exclusion_radius: float = 0.5,
    bounds: tuple[float, float, float, float] | None = None,
) -> tuple[float, float] | None:
    """Finds the closest useful frontier cell (free cell adjacent to unknown space).

    Cells closer than min_distance to the robot are skipped: navigating to
    (essentially) the current pose succeeds instantly without expanding the
    map, which would loop forever. Previously attempted frontiers are skipped
    via the exclusion list for the same reason.

    Args:
        grid: Latest occupancy grid from SLAM Toolbox.
        robot_x: Robot's current x position in the map frame.
        robot_y: Robot's current y position in the map frame.
        min_distance: Minimum distance from the robot for a frontier to count.
        excluded: Previously attempted frontier points to skip.
        exclusion_radius: Radius around excluded points to skip.
        bounds: Optional (x_min, y_min, x_max, y_max) restricting the search
            to a named zone; frontiers outside are ignored.

    Returns:
        (x, y) of the nearest acceptable frontier in the map frame, or None
        if no frontier cells remain.
    """
    width = grid.info.width
    height = grid.info.height
    resolution = grid.info.resolution
    origin_x = grid.info.origin.position.x
    origin_y = grid.info.origin.position.y
    data = grid.data
    min_dist_sq = min_distance ** 2
    exclusion_radius_sq = exclusion_radius ** 2
    excluded = excluded or []

    best = None
    best_dist = float('inf')
    for row in range(1, height - 1):
        for col in range(1, width - 1):
            index = row * width + col
            if data[index] != 0:
                continue
            if not _has_unknown_neighbor(data, width, height, row, col):
                continue
            cell_x = origin_x + (col + 0.5) * resolution
            cell_y = origin_y + (row + 0.5) * resolution
            if bounds is not None and not (
                bounds[0] <= cell_x <= bounds[2] and bounds[1] <= cell_y <= bounds[3]
            ):
                continue
            dist = (cell_x - robot_x) ** 2 + (cell_y - robot_y) ** 2
            if dist < min_dist_sq or dist >= best_dist:
                continue
            if any(
                (cell_x - ex) ** 2 + (cell_y - ey) ** 2 < exclusion_radius_sq
                for ex, ey in excluded
            ):
                continue
            best_dist = dist
            best = (cell_x, cell_y)
    return best


def _has_unknown_neighbor(data, width: int, height: int, row: int, col: int) -> bool:
    for d_row in (-1, 0, 1):
        for d_col in (-1, 0, 1):
            n_row, n_col = row + d_row, col + d_col
            if 0 <= n_row < height and 0 <= n_col < width:
                if data[n_row * width + n_col] == UNKNOWN:
                    return True
    return False
