"""Which localizer the running stack uses — pure logic, no ROS imports.

A live map is localized by SLAM Toolbox (ADR-004); a saved map loaded read-only
by AMCL on map_server's image (ADR-035). Nav2's SimpleCommander has to be told
which lifecycle node to wait for, and its AMCL wait also publishes an initial
pose at the origin every time it runs before AMCL answers — which would throw
away the pose of a robot someone already drove. So the skill finds the
localizer by name and waits for it to be active, nothing more.
"""

from __future__ import annotations

LOCALIZERS = ('amcl', 'slam_toolbox')


def localizer_node(node_names: list[str]) -> str | None:
    """Picks the localization node present in the ROS graph.

    Args:
        node_names: Node names as rclpy's get_node_names() returns them.

    Returns:
        'amcl' or 'slam_toolbox', or None while neither has appeared. AMCL wins
        if both are listed: SLAM Toolbox does not run next to it in any launch
        of this project, so a stale entry is the likelier explanation.
    """
    names = {name.lstrip('/') for name in node_names}
    for localizer in LOCALIZERS:
        if localizer in names:
            return localizer
    return None
