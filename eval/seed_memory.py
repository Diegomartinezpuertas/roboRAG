"""Seeds the RAG semantic_map with three distinct landmark coordinates.

For the planning benchmark (ADR-013) the landmarks only need to be distinct,
plausible free-space coordinates the planner can be told about via RAG — no
driving required (driving on this WSL2/software-rendered sim is unreliable: the
robot wedges against walls). We pick well-separated obstacle-free cells from the
SLAM map and register each directly in semantic_map.

Landmarks go in semantic_map ONLY (not zones): a zone is resolvable from SQLite
without RAG, which would mask the effect the benchmark isolates.

Writes eval/landmarks.json = {name: {"x": .., "y": ..}}.
"""

import json
import math
from pathlib import Path

import rclpy

from robot_zones.zone_store import ZoneStore

from bench_lib import BenchNode, spin_in_thread

LANDMARKS_FILE = Path(__file__).resolve().parent / 'landmarks.json'
ZONES_DB = '/home/diego/robot_ws/data/zones.db'
CONTROL_ZONE = 'base'        # known-zone control task target
SAFE_MARGIN_CELLS = 3    # ~0.15 m clearance; landmarks need only be plausible
MIN_SEPARATION_M = 1.2


def _cell_is_safe(grid, row, col, margin):
    width, height, data = grid.info.width, grid.info.height, grid.data
    for d_row in range(-margin, margin + 1):
        for d_col in range(-margin, margin + 1):
            r, c = row + d_row, col + d_col
            if not (0 <= r < height and 0 <= c < width):
                return False
            if data[r * width + c] != 0:
                return False
    return True


def find_safe_cell_near(grid, target_x, target_y, exclude=None, margin=SAFE_MARGIN_CELLS):
    """Returns the (x, y) of the obstacle-free cell closest to (target_x, target_y).

    Cells within MIN_SEPARATION_M of any point in `exclude` are skipped so
    successive landmarks stay distinct.
    """
    exclude = exclude or []
    info = grid.info
    res, ox, oy = info.resolution, info.origin.position.x, info.origin.position.y
    best, best_d = None, float('inf')
    for row in range(info.height):
        for col in range(info.width):
            if grid.data[row * info.width + col] != 0:
                continue
            if not _cell_is_safe(grid, row, col, margin):
                continue
            cx = ox + (col + 0.5) * res
            cy = oy + (row + 0.5) * res
            if any(math.dist((cx, cy), e) < MIN_SEPARATION_M for e in exclude):
                continue
            d = math.dist((cx, cy), (target_x, target_y))
            if d < best_d:
                best_d, best = d, (cx, cy)
    return best


def main():
    rclpy.init()
    node = BenchNode()
    spin_in_thread(node)

    node.get_logger().info('Waiting for SLAM map...')
    grid = None
    for _ in range(60):
        grid = node.get_map()
        if grid is not None:
            break
        rclpy.spin_once(node, timeout_sec=1.0)
    if grid is None:
        node.get_logger().error('No map received; is the sim running?')
        return

    info = grid.info
    x_min = info.origin.position.x
    x_max = x_min + info.width * info.resolution
    y_mid = info.origin.position.y + info.height * info.resolution / 2.0
    span = x_max - x_min
    targets = [
        ('estacion_a', x_min + 0.15 * span, y_mid),
        ('estacion_b', x_min + 0.50 * span, y_mid),
        ('estacion_c', x_min + 0.85 * span, y_mid),
    ]

    landmarks = {}
    placed = []
    for name, tx, ty in targets:
        cell = find_safe_cell_near(grid, tx, ty, exclude=placed)
        if cell is None:
            node.get_logger().warning(f'{name}: no distinct safe cell found, skipping')
            continue
        ok = node.seed_semantic_object(
            object_id=f'landmark-{name}',
            label=name,
            x=cell[0], y=cell[1],
            description=f'{name}, a named location in the house',
        )
        placed.append(cell)
        landmarks[name] = {'x': round(cell[0], 3), 'y': round(cell[1], 3)}
        node.get_logger().info(f'{name}: seeded at ({cell[0]:.2f}, {cell[1]:.2f}) ok={ok}')

    LANDMARKS_FILE.write_text(json.dumps(landmarks, indent=2), encoding='utf-8')
    node.get_logger().info(f'Wrote {len(landmarks)} landmarks to {LANDMARKS_FILE}')

    # Known-zone control: a zone lives in SQLite, resolvable WITHOUT RAG. The
    # zone_nav control task should succeed in both conditions, showing the
    # ablation isolates RAG's object-memory rather than navigation in general.
    if 'estacion_b' in landmarks:
        b = landmarks['estacion_b']
        ZoneStore(ZONES_DB).save(CONTROL_ZONE, {
            'x_min': b['x'] - 0.3, 'y_min': b['y'] - 0.3,
            'x_max': b['x'] + 0.3, 'y_max': b['y'] + 0.3,
        })
        node.get_logger().info(f'Seeded control zone "{CONTROL_ZONE}" around estacion_b')

    rclpy.shutdown()


if __name__ == '__main__':
    main()
