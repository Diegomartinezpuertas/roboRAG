"""Launches the full robot system: simulation + navigation stack + cognitive agent."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


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

    simulation_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(bringup_dir, 'launch', 'simulation.launch.py'),
        ),
        launch_arguments={
            'use_nav2': use_nav2,
            'use_rviz': use_rviz,
            'use_gz_gui': use_gz_gui,
        }.items(),
    )

    agent_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(bringup_dir, 'launch', 'agent.launch.py'),
        ),
    )

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
            description='Open the Gazebo GUI (drops sim real-time factor to ~0.15 on WSL2)',
        ),
        simulation_launch,
        agent_launch,
    ])
