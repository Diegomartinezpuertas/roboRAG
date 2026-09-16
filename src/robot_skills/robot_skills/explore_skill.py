"""Frontier exploration: finds unexplored edges in the occupancy grid and picks where to go.

Two selection rules live here. `find_nearest_frontier` is the original: the
closest frontier cell to the robot. Measured live on the TurtleBot3 house it is
a poor explorer — five minutes mapped 9.7 x 4.4 m, because the nearest frontier
is almost always the next cell along the wall the robot is already beside, so
it creeps along one wall instead of opening rooms.

`find_best_frontier` is what `explore` uses now: frontier cells are grouped
into connected clusters, and the robot heads for the cluster with the most
unexplored edge per metre of travel. A doorway into an unmapped room is a long
frontier; a speck of noise beside a wall is a short one. See
docs/decisions/ADR-027-exploration-frontier-clusters.md.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only a type hint; guarding it keeps this pure-logic module importable
    # (and unit-testable) without a ROS environment.
    from nav_msgs.msg import OccupancyGrid

UNKNOWN = -1

# Frontier clusters smaller than this many cells are scan noise (a single
# unknown cell beside a wall), not a place worth driving to.
MIN_CLUSTER_CELLS = 4

# Clearance, in meters, beyond which a target counts as fully clear of walls:
# Nav2's inflation radius. A frontier target closer to a wall than this sits in
# inflated cost, and a goal there can fail to plan even though the robot could
# drive past the same spot — the failure measured live (ADR-027).
TARGET_CLEARANCE_M = 0.5
OCCUPIED_THRESHOLD = 50


@dataclass(frozen=True)
class FrontierCluster:
    """A connected run of frontier cells — one unexplored edge of the map.

    Attributes:
        size: Number of frontier cells; with the map resolution, its length.
        target: The member cell with the most clearance from obstacles (capped
            at TARGET_CLEARANCE_M), ties broken by nearness to the centroid, in
            map-frame meters. A member cell, because the centroid of a curved
            frontier can lie inside a wall; the clearest one, because a goal
            against a wall sits in inflated cost and fails to plan.
        cells: Every member as (x, y, clearance in cells), in the same ranking
            order as `target` (best first), so a caller that must skip some
            members — too close to the robot, already attempted — can take the
            best remaining one instead of discarding the whole cluster.
    """

    size: int
    target: tuple[float, float]
    cells: tuple[tuple[float, float, int], ...] = ()


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


def find_frontier_clusters(
    grid: OccupancyGrid,
    bounds: tuple[float, float, float, float] | None = None,
    min_cells: int = MIN_CLUSTER_CELLS,
) -> list[FrontierCluster]:
    """Groups frontier cells into 8-connected clusters.

    Args:
        grid: Latest occupancy grid from SLAM Toolbox.
        bounds: Optional (x_min, y_min, x_max, y_max); cells outside are ignored.
        min_cells: Clusters with fewer cells are dropped as noise.

    Returns:
        Clusters of at least `min_cells` cells, largest first.
    """
    width, height = grid.info.width, grid.info.height
    resolution = grid.info.resolution
    origin_x, origin_y = grid.info.origin.position.x, grid.info.origin.position.y
    data = grid.data

    def center(index: int) -> tuple[float, float]:
        row, col = divmod(index, width)
        return origin_x + (col + 0.5) * resolution, origin_y + (row + 0.5) * resolution

    frontier = set()
    for row in range(1, height - 1):
        for col in range(1, width - 1):
            index = row * width + col
            if data[index] != 0 or not _has_unknown_neighbor(data, width, height, row, col):
                continue
            if bounds is not None:
                x, y = center(index)
                if not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]):
                    continue
            frontier.add(index)

    clusters: list[FrontierCluster] = []
    seen: set[int] = set()
    for start in frontier:
        if start in seen:
            continue
        members = []
        queue = deque([start])
        seen.add(start)
        while queue:
            index = queue.popleft()
            members.append(index)
            row, col = divmod(index, width)
            for d_row in (-1, 0, 1):
                for d_col in (-1, 0, 1):
                    neighbor = (row + d_row) * width + (col + d_col)
                    if neighbor in frontier and neighbor not in seen:
                        seen.add(neighbor)
                        queue.append(neighbor)
        if len(members) < min_cells:
            continue
        points = [center(i) for i in members]
        cx = sum(x for x, _ in points) / len(points)
        cy = sum(y for _, y in points) / len(points)
        reach = max(1, int(round(TARGET_CLEARANCE_M / resolution)))
        ranked = sorted(
            ((*center(i), _clearance_cells(data, width, height, i, reach)) for i in members),
            key=lambda c: (-c[2], (c[0] - cx) ** 2 + (c[1] - cy) ** 2),
        )
        clusters.append(FrontierCluster(
            size=len(members), target=(ranked[0][0], ranked[0][1]), cells=tuple(ranked),
        ))
    return sorted(clusters, key=lambda c: -c.size)


def find_best_frontier(
    grid: OccupancyGrid,
    robot_x: float,
    robot_y: float,
    min_distance: float = 0.6,
    excluded: list[tuple[float, float]] | None = None,
    exclusion_radius: float = 0.5,
    bounds: tuple[float, float, float, float] | None = None,
) -> tuple[float, float] | None:
    """Picks the frontier worth the most unexplored edge per metre of travel.

    Each cluster scores size / (1 + distance to its target): a long frontier a
    little further away beats a short one next to the robot, which is exactly
    the choice the nearest-cell rule got wrong. A cluster's target is its best
    member that is at least min_distance from the robot and not near a
    previously attempted target — not its single best member overall: at
    startup the robot often stands inside one large frontier whose clearest
    cell is right beside it, and skipping the whole cluster for that ended
    exploration on its first step (found when re-running the benchmark).

    Args:
        grid: Latest occupancy grid from SLAM Toolbox.
        robot_x: Robot's current x position in the map frame.
        robot_y: Robot's current y position in the map frame.
        min_distance: Minimum distance from the robot for a target to count.
        excluded: Previously attempted targets to skip.
        exclusion_radius: Radius around excluded points to skip.
        bounds: Optional (x_min, y_min, x_max, y_max) restricting the search.

    Returns:
        (x, y) target of the best-scoring cluster, or None if none is left.
    """
    excluded = excluded or []
    best, best_score = None, -1.0
    for cluster in find_frontier_clusters(grid, bounds=bounds):
        for tx, ty, _clearance in cluster.cells:
            distance = math.hypot(tx - robot_x, ty - robot_y)
            if distance < min_distance:
                continue
            if any(math.hypot(tx - ex, ty - ey) < exclusion_radius for ex, ey in excluded):
                continue
            score = cluster.size / (1.0 + distance)
            if score > best_score:
                best, best_score = (tx, ty), score
            break                                   # best eligible member of this cluster
    return best


def _clearance_cells(data, width: int, height: int, index: int, reach: int) -> int:
    """Chebyshev distance, in cells, from a cell to the nearest occupied cell, capped at reach."""
    row, col = divmod(index, width)
    for ring in range(1, reach + 1):
        for d_row in range(-ring, ring + 1):
            for d_col in (-ring, ring) if abs(d_row) != ring else range(-ring, ring + 1):
                n_row, n_col = row + d_row, col + d_col
                if 0 <= n_row < height and 0 <= n_col < width and \
                        data[n_row * width + n_col] >= OCCUPIED_THRESHOLD:
                    return ring - 1
    return reach
