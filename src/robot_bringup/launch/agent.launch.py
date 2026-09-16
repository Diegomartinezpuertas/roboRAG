"""Launches the cognitive agent stack: rag_node, skills_executor_node, llm_planner_node, dashboard_node."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    """Builds the launch description for the RAG, skills, and LLM planner nodes."""
    use_sim_time = LaunchConfiguration('use_sim_time', default='true')
    # The memory session to pin (ADR-019, ADR-028). full_system passes the saved
    # map's id, or the fresh map's frame id. Empty keeps the persisted session —
    # what an agent-only run (offline benchmark, ADR-020) wants.
    memory_session = LaunchConfiguration('memory_session', default='')

    agent_params_file = os.path.join(
        get_package_share_directory('robot_bringup'), 'config', 'agent_params.yaml',
    )

    # respawn=True on all agent nodes: a crash in one restarts just that node
    # instead of taking down the whole launch (the simulation stack, which is
    # slow to bring back up, keeps running).
    rag_node = Node(
        package='robot_rag',
        executable='rag_node',
        name='rag_node',
        output='screen',
        parameters=[
            agent_params_file,
            {'use_sim_time': use_sim_time, 'map_session_id': memory_session},
        ],
        respawn=True,
        respawn_delay=2.0,
    )

    # No `name=` here: this process hosts a second internal node (Nav2's
    # BasicNavigator), and a launch-level name remap would rename both to
    # 'skills_executor_node', colliding in the graph. The node names itself.
    skills_executor_node = Node(
        package='robot_skills',
        executable='skills_executor_node',
        output='screen',
        parameters=[agent_params_file, {'use_sim_time': use_sim_time}],
        respawn=True,
        respawn_delay=2.0,
    )

    llm_planner_node = Node(
        package='robot_brain',
        executable='llm_planner_node',
        name='llm_planner_node',
        output='screen',
        parameters=[agent_params_file, {'use_sim_time': use_sim_time}],
        respawn=True,
        respawn_delay=2.0,
    )

    dashboard_node = Node(
        package='robot_dashboard',
        executable='dashboard_node',
        name='dashboard_node',
        output='screen',
        parameters=[agent_params_file, {'use_sim_time': use_sim_time}],
        respawn=True,
        respawn_delay=2.0,
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            'use_sim_time', default_value='true',
            description='Use Gazebo simulation clock',
        ),
        DeclareLaunchArgument(
            'memory_session', default_value='',
            description='Pin rag_node to this memory session id (empty keeps the persisted one)',
        ),
        rag_node,
        skills_executor_node,
        llm_planner_node,
        dashboard_node,
    ])
