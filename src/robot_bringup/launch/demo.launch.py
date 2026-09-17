"""Launches the system the way the demo is recorded: every window open, the house already mapped.

`full_system.launch.py` with three defaults changed:

- **use_gz_gui:=true** — the Gazebo window, so the simulated house is on screen
  next to RViz and the dashboard. On WSL2 it renders on llvmpipe and costs
  real-time factor (measured 2026-09-16: ~0.68 headless, ~0.59 with the window;
  ~0.15 on the setup first measured). The dashboard shows the live factor next
  to the drive controls; pass use_gz_gui:=false to get the speed back.
- **use_rviz:=true** — map, LIDAR and Nav2 costmaps.
- **saved_map:=house** — SLAM starts from the map saved as `house`: yours in
  data/maps/house (save it from the dashboard), or one shipped in
  robot_bringup/maps/house. No mapping phase before recording, and the memory
  session is pinned to it. With no `house` map anywhere, the launch fails and
  says where it looked. SLAM keeps mapping from it, so a take can still move
  its walls; every launch starts from the saved file again, and
  saved_map_mode:=localization loads it read-only instead (ADR-035).

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
            description='Gazebo window (costs real-time factor on WSL2; ~0.68 -> ~0.59 measured)',
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
        DeclareLaunchArgument(
            'saved_map_mode', default_value='mapping',
            description='mapping (SLAM Toolbox) keeps adding scans; localization '
                        '(map_server + AMCL) leaves the map exactly as saved',
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
                'saved_map_mode': LaunchConfiguration('saved_map_mode'),
            }.items(),
        ),
    ])
