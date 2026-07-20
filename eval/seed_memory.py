"""Seeds the RAG semantic_map with distinct landmark coordinates.

For the planning benchmark (ADR-013) the landmarks only need to be distinct,
plausible free-space coordinates the planner can be told about via RAG — no
driving required (driving on this WSL2/software-rendered sim is unreliable: the
robot wedges against walls). We pick well-separated obstacle-free cells from the
SLAM map and register each directly in semantic_map.

Landmarks go in semantic_map ONLY (not zones): a zone is resolvable from SQLite
without RAG, which would mask the effect the benchmark isolates.

Usage:
    python3 seed_memory.py           # 3 landmarks — the tasks_full/phrasing suites
    python3 seed_memory.py --hard    # + confusable distractors for tasks_hard

The distractors are opt-in on purpose. They add competing entries to semantic
memory, which changes retrieval for *every* query — so seeding them by default
would silently invalidate the committed tasks_full results, which were measured
against a three-landmark memory. See docs/EVALUATION.md.

Writes eval/landmarks.json = {name: {"x": .., "y": ..}}.
"""

import argparse
import json
import math
import os
import time
from pathlib import Path

import rclpy

from robot_zones.zone_store import ZoneStore

from bench_lib import BenchNode, spin_in_thread

HERE = Path(__file__).resolve().parent
LANDMARKS_FILE = HERE / 'landmarks.json'
# Workspace root: ROBOT_WS when the shell was prepared with setup_env.sh,
# otherwise the parent of eval/ — which is the workspace root by layout.
WS_ROOT = Path(os.environ.get('ROBOT_WS', HERE.parent))
ZONES_DB = str(WS_ROOT / 'data' / 'zones.db')
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--hard', action='store_true',
        help='also seed the confusable distractor landmarks used by tasks_hard.yaml',
    )
    args = parser.parse_args()

    rclpy.init()
    node = BenchNode()
    spin = spin_in_thread(node)

    node.get_logger().info('Waiting for SLAM map...')
    grid = None
    for _ in range(60):
        grid = node.get_map()
        if grid is not None:
            break
        # Plain sleep: the node is already spinning on its own executor thread
        # (spin_in_thread); never nest rclpy.spin_once on top — see ADR-007.
        time.sleep(1.0)
    if grid is None:
        node.get_logger().error('No map received; is the sim running?')
        spin.stop()
        rclpy.shutdown()
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
    if args.hard:
        # Name-confusable neighbours for the distractor_nav tasks. Placed away
        # from the landmark they shadow so NAV_TOLERANCE_M can tell a correct
        # choice from a wrong one; retrieval, however, will happily return both
        # for "ve a estacion_a", which is the point.
        targets += [
            ('estacion_a_norte', x_min + 0.30 * span, y_mid),
            ('estacion_c_sur', x_min + 0.70 * span, y_mid),
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

    # Controlled scene descriptors for the attribute_nav tasks ("go to the
    # white open room"). Two landmarks get contrasting attributes, in the
    # same format the robot's own perceive skill stores autonomously.
    descriptors = {
        'estacion_a': 'predominantly white, an open, uncluttered space',
        'estacion_c': (
            'predominantly brown, a cluttered space with many objects '
            '(9 obstacle groups nearby)'
        ),
    }
    if args.hard:
        # Deliberately similar wording to the landmarks they shadow: the
        # planner has to disambiguate on the NAME, not on the description.
        descriptors.update({
            'estacion_a_norte': 'predominantly white, a fairly open space',
            'estacion_c_sur': (
                'predominantly brown, a somewhat cluttered space with several '
                'objects (6 obstacle groups nearby)'
            ),
        })
    for name, desc in descriptors.items():
        if name in landmarks:
            lm = landmarks[name]
            ok = node.seed_semantic_object(
                object_id=f'scene-seed-{name}',
                label='area',
                x=lm['x'], y=lm['y'],
                description=desc,
            )
            node.get_logger().info(f'scene descriptor at {name}: ok={ok}')

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

    spin.stop()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
