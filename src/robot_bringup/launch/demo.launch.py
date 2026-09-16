"""Launches the system the way the demo is recorded: every window open, the house already mapped.

`full_system.launch.py` with three defaults changed:

- **use_gz_gui:=true** — the Gazebo window, so the simulated house is on screen
  next to RViz and the dashboard. On WSL2 it renders on llvmpipe and drops the
  real-time factor to roughly 0.15: navigation visibly slows. That is the price
  of the shot; pass use_gz_gui:=false to get the speed back.
- **use_rviz:=true** — map, LIDAR and Nav2 costmaps.
- **saved_map:=house** — SLAM starts from the map shipped in
  robot_bringup/maps/house, so there is no mapping phase before recording, and
  the memory session is pinned to it.

Every argument can still be overridden, e.g. `saved_map:=casa` for a map you
saved yourself. See docs/decisions/ADR-026-shipped-map-and-demo-launch.md.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description() -> LaunchDescription:
    """Builds the demo launch: full system, Gazebo GUI and RViz on, shipped map loaded."""
    bringup_dir = get_package_share_directory('robot_bringup')
    return LaunchDescription([
        DeclareLaunchArgument(
            'use_gz_gui', default_value='true',
            description='Gazebo window (on WSL2 costs real-time factor: ~1.0 -> ~0.15)',
        ),
        DeclareLaunchArgument(
            'use_rviz', default_value='true', description='RViz with map, LIDAR and costmaps',
        ),
        DeclareLaunchArgument(
            'use_nav2', default_value='true', description='Nav2 alongside SLAM',
        ),
        DeclareLaunchArgument(
            'saved_map', default_value='house',
            description='Saved map id to start from (shipped: house); empty maps from scratch',
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(bringup_dir, 'launch', 'full_system.launch.py'),
            ),
            launch_arguments={
                'use_gz_gui': LaunchConfiguration('use_gz_gui'),
                'use_rviz': LaunchConfiguration('use_rviz'),
                'use_nav2': LaunchConfiguration('use_nav2'),
                'saved_map': LaunchConfiguration('saved_map'),
            }.items(),
        ),
    ])
