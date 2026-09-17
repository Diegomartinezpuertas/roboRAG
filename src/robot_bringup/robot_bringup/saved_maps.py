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

A saved map loads into SLAM Toolbox by default, which keeps mapping from there
(ADR-026): on the hand-made maps this project produces, that is what navigates.
`localization` mode instead serves the saved occupancy image (`map.yaml` +
`map.pgm`) through Nav2's map_server and localizes on it with AMCL, so the map
cannot change — measured, and measured to navigate worse here, because those
maps carry 0.3–0.5 m of distortion (ADR-035).

See docs/decisions/ADR-026-shipped-map-and-demo-launch.md and
docs/decisions/ADR-035-saved-map-loads-read-only.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# The simulated world and spawn pose. A SLAM map built from scratch has its
# origin where the robot spawned, so (world, spawn) *is* its coordinate frame —
# and the memory session id of every map built from scratch (ADR-028).
DEFAULT_WORLD = 'turtlebot3_house'
DEFAULT_SPAWN_X = -2.0
DEFAULT_SPAWN_Y = -0.5

# How a saved map is loaded. `localization`: map_server + AMCL, so nothing can
# change the map. `mapping`: SLAM Toolbox continues the pose graph, which keeps
# the map aligned with what the robot sees now, at the cost of drawing over the
# saved walls where the two disagree (ADR-035).
SAVED_MAP_MODES = ('localization', 'mapping')
DEFAULT_SAVED_MAP_MODE = 'localization'

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


def check_saved_map_mode(mode: str) -> str:
    """Validates the saved_map_mode launch argument.

    Args:
        mode: 'localization' or 'mapping' (surrounding spaces ignored).

    Returns:
        The mode, stripped.

    Raises:
        ValueError: For any other value — a typo must not silently pick a mode.
    """
    mode = mode.strip()
    if mode not in SAVED_MAP_MODES:
        raise ValueError(
            f'Invalid saved_map_mode {mode!r}: use {" or ".join(SAVED_MAP_MODES)}',
        )
    return mode


def uses_amcl(saved_map: str, mode: str = DEFAULT_SAVED_MAP_MODE) -> bool:
    """Tells whether a launch localizes with map_server + AMCL instead of running SLAM.

    Only `saved_map_mode:=localization` on a saved map does.

    Args:
        saved_map: The saved_map launch argument ('' when mapping from scratch).
        mode: The saved_map_mode launch argument, validated even without a saved map.

    Returns:
        True only for a saved map in localization mode.

    Raises:
        ValueError: If mode is not one of SAVED_MAP_MODES.
    """
    return check_saved_map_mode(mode) == 'localization' and bool(saved_map.strip())


def resolve_map_image(map_base: Path) -> Path:
    """Finds the occupancy image map_server loads for a saved map.

    `save_map` writes it next to the pose graph (map.yaml + the image it names).
    Maps saved before that have only the pose graph.

    Args:
        map_base: Base path from resolve_saved_map.

    Returns:
        The map.yaml path.

    Raises:
        FileNotFoundError: If map.yaml or the image it names is missing. The
            message says how to write them: load the map in mapping mode and
            save it again.
    """
    map_yaml = Path(map_base).with_suffix('.yaml')
    image = None
    if map_yaml.is_file():
        with open(map_yaml, encoding='utf-8') as source:
            image = (yaml.safe_load(source) or {}).get('image')
    if image and (map_yaml.parent / image).is_file():
        return map_yaml
    raise FileNotFoundError(
        f'Saved map {map_base.parent.name!r} has no occupancy image ({map_yaml} and the image '
        'it names), which read-only loading needs. Launch with saved_map_mode:=mapping and '
        'save the map again ("Guardar mapa") to write it.',
    )


def start_pose_in_zone(zone: str, zones: dict) -> tuple[float, float]:
    """The map-frame point a run should start at, the centre of a named zone.

    A saved map is normally started at its first node, which is wherever
    mapping began — in this house, a nook 0.25 m from a wall (ADR-036). Naming
    a zone instead starts the robot somewhere it can actually turn around.

    Args:
        zone: Zone name, e.g. "entrada".
        zones: Zones as ZoneStore.load_all() returns them.

    Returns:
        (x, y) in the map frame.

    Raises:
        ValueError: If the zone is unknown — with the known names, because a
            typo here would otherwise spawn the robot inside a wall.
    """
    area = zones.get(zone.strip())
    if area is None:
        known = ', '.join(sorted(zones)) or 'none stored'
        raise ValueError(f'Unknown start_zone {zone!r}; known zones: {known}')
    return ((area['x_min'] + area['x_max']) / 2.0, (area['y_min'] + area['y_max']) / 2.0)


def world_pose_for_map_point(
    map_x: float, map_y: float,
    spawn_x: float = DEFAULT_SPAWN_X, spawn_y: float = DEFAULT_SPAWN_Y,
) -> tuple[float, float]:
    """Converts a map-frame point into the simulator's world coordinates.

    A map built from scratch has its origin where the robot spawned, so the two
    frames differ by exactly that spawn pose (ADR-019, ADR-028).

    Args:
        map_x: X in the map frame.
        map_y: Y in the map frame.
        spawn_x: World x the map's origin corresponds to.
        spawn_y: World y the map's origin corresponds to.

    Returns:
        (x, y) in world coordinates, for spawning the robot there.
    """
    return (map_x + spawn_x, map_y + spawn_y)


def slam_params_for_saved_map(
    params: dict, map_base: Path, start_pose: tuple[float, float] | None = None,
) -> dict:
    """Returns SLAM Toolbox parameters that continue a saved map (saved_map_mode:=mapping).

    Without a start pose the robot is started at the pose graph's first node
    ("dock") — the pose mapping began at, which is the simulator's spawn pose.
    With one, SLAM is told where in the map the robot is starting instead, so a
    run can begin in a named zone. Either way SLAM keeps mapping from there, so
    the loaded map extends rather than freezes.

    Args:
        params: The parsed slam_params.yaml ({"slam_toolbox": {"ros__parameters": …}}).
        map_base: Base path from resolve_saved_map.
        start_pose: (x, y) in the map frame, or None for the dock start.

    Returns:
        A new parameters dict; the input is not modified.
    """
    node = dict(params.get('slam_toolbox', {}))
    ros_params = dict(node.get('ros__parameters', {}))
    ros_params['map_file_name'] = str(map_base)
    # The two are mutually exclusive: SLAM Toolbox uses the pose when both are set.
    if start_pose is None:
        ros_params['map_start_at_dock'] = True
        ros_params.pop('map_start_pose', None)
    else:
        ros_params['map_start_pose'] = [float(start_pose[0]), float(start_pose[1]), 0.0]
        ros_params.pop('map_start_at_dock', None)
    node['ros__parameters'] = ros_params
    return {**params, 'slam_toolbox': node}


def nav2_params_for_start_pose(params: dict, start_pose: tuple[float, float]) -> dict:
    """Returns Nav2 parameters with AMCL's initial pose at a map-frame point.

    AMCL is told where the robot starts instead of assuming the map origin
    (`set_initial_pose`), so a read-only run can also begin in a named zone.

    Args:
        params: The parsed nav2_params.yaml.
        start_pose: (x, y) in the map frame.

    Returns:
        A new parameters dict; the input is not modified.
    """
    node = dict(params.get('amcl', {}))
    ros_params = dict(node.get('ros__parameters', {}))
    ros_params['set_initial_pose'] = True
    ros_params['initial_pose'] = {
        **dict(ros_params.get('initial_pose') or {}),
        'x': float(start_pose[0]), 'y': float(start_pose[1]), 'z': 0.0, 'yaw': 0.0,
    }
    node['ros__parameters'] = ros_params
    return {**params, 'amcl': node}


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
