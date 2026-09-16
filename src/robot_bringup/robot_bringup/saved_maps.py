"""Where a saved SLAM map lives, and how SLAM Toolbox is told to load it — no ROS imports.

A map saved with the `save_map` skill (or the dashboard's "Guardar mapa") is a
serialized SLAM Toolbox pose graph, `map.posegraph` + `map.data`, under a map
id (ADR-019). Two places can hold one:

1. `$ROBOT_WS/data/maps/<id>/` — maps saved on this machine (gitignored);
2. `<robot_bringup share>/maps/<id>/` — maps shipped with the repository, so a
   fresh clone can start on a known house without mapping it first.

The workspace wins: re-saving a shipped map's id locally replaces it for you
without touching the repository. Kept free of launch/rclpy imports so the
resolution rules are unit-tested in layer 1 (ADR-018).

See docs/decisions/ADR-026-shipped-map-and-demo-launch.md.
"""

from __future__ import annotations

import re
from pathlib import Path

# The simulated world and spawn pose. A SLAM map built from scratch has its
# origin where the robot spawned, so (world, spawn) *is* its coordinate frame —
# and the memory session id of every map built from scratch (ADR-028).
DEFAULT_WORLD = 'turtlebot3_house'
DEFAULT_SPAWN_X = -2.0
DEFAULT_SPAWN_Y = -0.5

# Same alphabet the dashboard's name normalization produces, minus anything
# that could walk out of the maps directory.
_VALID_MAP_ID = re.compile(r'^[\w.-]+$')


def map_search_paths(map_id: str, ws_root: Path, share_dir: Path) -> list[Path]:
    """Returns the candidate base paths (without extension) for a map id, in priority order.

    Args:
        map_id: Saved map id, e.g. "house".
        ws_root: Workspace root (ROBOT_WS).
        share_dir: The robot_bringup package's installed share directory.

    Returns:
        Base paths such that `<base>.posegraph` and `<base>.data` are the files.
    """
    return [
        Path(ws_root) / 'data' / 'maps' / map_id / 'map',
        Path(share_dir) / 'maps' / map_id / 'map',
    ]


def resolve_saved_map(map_id: str, ws_root: Path, share_dir: Path) -> Path:
    """Finds the saved map to load for a map id.

    Args:
        map_id: Saved map id, e.g. "house".
        ws_root: Workspace root (ROBOT_WS).
        share_dir: The robot_bringup package's installed share directory.

    Returns:
        The base path (without extension) SLAM Toolbox's `map_file_name` takes.

    Raises:
        ValueError: If the id is empty or could escape the maps directory.
        FileNotFoundError: If no location holds both files — the message lists
            where it looked, so a typo is obvious at launch instead of SLAM
            silently starting an empty map.
    """
    if not map_id or map_id in ('.', '..') or not _VALID_MAP_ID.match(map_id):
        raise ValueError(f'Invalid saved map id: {map_id!r}')
    candidates = map_search_paths(map_id, ws_root, share_dir)
    for base in candidates:
        if base.with_suffix('.posegraph').is_file() and base.with_suffix('.data').is_file():
            return base
    looked = ', '.join(str(base.parent) for base in candidates)
    raise FileNotFoundError(f'No saved map "{map_id}" (map.posegraph + map.data) in: {looked}')


def slam_params_for_saved_map(params: dict, map_base: Path) -> dict:
    """Returns SLAM Toolbox parameters that start from a saved map instead of an empty one.

    The robot is started at the pose graph's first node ("dock") — the pose
    mapping began at, which is the simulator's spawn pose — and SLAM keeps
    mapping from there, so the loaded map extends rather than freezes.

    Args:
        params: The parsed slam_params.yaml ({"slam_toolbox": {"ros__parameters": …}}).
        map_base: Base path from resolve_saved_map.

    Returns:
        A new parameters dict; the input is not modified.
    """
    node = dict(params.get('slam_toolbox', {}))
    ros_params = dict(node.get('ros__parameters', {}))
    ros_params['map_file_name'] = str(map_base)
    ros_params['map_start_at_dock'] = True
    ros_params.pop('map_start_pose', None)   # mutually exclusive with the dock start
    node['ros__parameters'] = ros_params
    return {**params, 'slam_toolbox': node}


def fresh_map_session_id(world: str, spawn_x: float, spawn_y: float) -> str:
    """Memory session id for a SLAM map built from scratch.

    A fresh map's frame is fixed by where the robot spawned in which world, so
    two fresh maps from the same spawn share coordinates and may share memories,
    while a different world or spawn pose must not see them (ADR-019). The id
    is deterministic for that reason: the old "continue whatever session was
    persisted last" kept memories across a change of spawn pose, and minting a
    new id on every launch would throw away memories that are still valid.

    Args:
        world: World name, e.g. "turtlebot3_house".
        spawn_x: Spawn x in world meters.
        spawn_y: Spawn y in world meters.

    Returns:
        An id such as "fresh_turtlebot3_house_x-2.00_y-0.50" — valid as a saved
        map id too, since `save_map` without a name saves under the session id.
    """
    safe_world = re.sub(r'[^\w.-]', '_', world) or 'world'
    return f'fresh_{safe_world}_x{float(spawn_x):.2f}_y{float(spawn_y):.2f}'


def memory_session_for_launch(saved_map: str, world: str, spawn_x: float, spawn_y: float) -> str:
    """The memory session a launch should pin: the saved map's id, else the fresh frame's.

    Args:
        saved_map: The saved_map launch argument ('' when mapping from scratch).
        world: World name.
        spawn_x: Spawn x in world meters.
        spawn_y: Spawn y in world meters.

    Returns:
        The session id to pass to rag_node as map_session_id.
    """
    saved_map = saved_map.strip()
    return saved_map if saved_map else fresh_map_session_id(world, spawn_x, spawn_y)
