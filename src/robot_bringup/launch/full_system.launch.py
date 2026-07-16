"""Launches the full robot system: simulation + navigation stack + cognitive agent."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource


def generate_launch_description() -> LaunchDescription:
    """Builds the launch description that brings up the entire robot system."""
    bringup_dir = get_package_share_directory('robot_bringup')

    simulation_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(bringup_dir, 'launch', 'simulation.launch.py'),
        ),
    )

    agent_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(bringup_dir, 'launch', 'agent.launch.py'),
        ),
    )

    return LaunchDescription([
        simulation_launch,
        agent_launch,
    ])
