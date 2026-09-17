"""Launches the full robot system: simulation + navigation stack + cognitive agent."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

from robot_bringup.saved_maps import (
    DEFAULT_SPAWN_X,
    DEFAULT_SPAWN_Y,
    DEFAULT_WORLD,
    memory_session_for_launch,
)


def _agent(context, bringup_dir: str) -> list:
    """Includes the agent with its memory session pinned to this launch's map frame.

    A saved map pins its own id; a map built from scratch pins the id of its
    frame — world plus spawn pose — so memories carry over between fresh maps
    of the same frame and never leak into another (ADR-028).
    """
    session = memory_session_for_launch(
        LaunchConfiguration('saved_map').perform(context),
        DEFAULT_WORLD,
        float(LaunchConfiguration('x_pose', default=str(DEFAULT_SPAWN_X)).perform(context)),
        float(LaunchConfiguration('y_pose', default=str(DEFAULT_SPAWN_Y)).perform(context)),
    )
    return [
        LogInfo(msg=f'Memory session: {session}'),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(bringup_dir, 'launch', 'agent.launch.py')),
            launch_arguments={'memory_session': session}.items(),
        ),
    ]


def generate_launch_description() -> LaunchDescription:
    """Builds the launch description that brings up the entire robot system."""
    bringup_dir = get_package_share_directory('robot_bringup')

    # Declared and forwarded explicitly rather than relying on scope inheritance,
    # so `full_system.launch.py use_nav2:=false` does what it says. That is the
    # manual-mapping run: SLAM + the dashboard, no autonomous navigation
    # competing for /cmd_vel while someone drives with WASD (ADR-023).
    use_nav2 = LaunchConfiguration('use_nav2', default='true')
    use_rviz = LaunchConfiguration('use_rviz', default='true')
    use_gz_gui = LaunchConfiguration('use_gz_gui', default='false')
    # One id, two consumers: SLAM loads the map, rag_node pins its memory
    # session to it — so the map and the memories written on it stay paired.
    saved_map = LaunchConfiguration('saved_map', default='')
    saved_map_mode = LaunchConfiguration('saved_map_mode', default='localization')
    start_zone = LaunchConfiguration('start_zone', default='')

    simulation_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(bringup_dir, 'launch', 'simulation.launch.py'),
        ),
        launch_arguments={
            'use_nav2': use_nav2,
            'use_rviz': use_rviz,
            'use_gz_gui': use_gz_gui,
            'saved_map': saved_map,
            'saved_map_mode': saved_map_mode,
            'start_zone': start_zone,
        }.items(),
    )

    agent_launch = OpaqueFunction(function=_agent, args=[bringup_dir])

    return LaunchDescription([
        DeclareLaunchArgument(
            'use_nav2', default_value='true',
            description='Start Nav2 alongside SLAM (false for a manual mapping run)',
        ),
        DeclareLaunchArgument(
            'use_rviz', default_value='true',
            description='Open RViz with map + lidar + costmaps view',
        ),
        DeclareLaunchArgument(
            'use_gz_gui', default_value='false',
            description='Open the Gazebo GUI (costs real-time factor on WSL2; ~0.68 -> ~0.59 measured)',
        ),
        DeclareLaunchArgument(
            'saved_map', default_value='',
            description='Start from a saved map id (SLAM + memory session); empty maps from scratch',
        ),
        DeclareLaunchArgument(
            'start_zone', default_value='',
            description="Start the robot at this zone's centre instead of where the map begins",
        ),
        DeclareLaunchArgument(
            'saved_map_mode', default_value='localization',
            description='With saved_map: mapping (SLAM Toolbox) keeps adding scans; '
                        'localization (map_server + AMCL) leaves the map exactly as saved',
        ),
        simulation_launch,
        agent_launch,
    ])
