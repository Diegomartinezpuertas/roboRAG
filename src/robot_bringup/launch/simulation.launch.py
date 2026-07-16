"""Launches Gazebo (headless) + TurtleBot3 Waffle + SLAM Toolbox + Nav2 + RViz.

The Gazebo GUI is disabled by default: under WSL2 it renders on llvmpipe and
drags the real-time factor down to ~0.15. RViz (use_rviz, default true) is the
intended visualization — map, lidar scan, costmaps — and keeps RTF at ~1.0.
Pass use_gz_gui:=true to get the Gazebo window back.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    AppendEnvironmentVariable,
    DeclareLaunchArgument,
    IncludeLaunchDescription,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    """Builds the launch description for the simulated environment and navigation stack."""
    use_sim_time = LaunchConfiguration('use_sim_time', default='true')
    use_nav2 = LaunchConfiguration('use_nav2', default='true')
    use_rviz = LaunchConfiguration('use_rviz', default='true')
    use_gz_gui = LaunchConfiguration('use_gz_gui', default='false')
    x_pose = LaunchConfiguration('x_pose', default='-2.0')
    y_pose = LaunchConfiguration('y_pose', default='-0.5')

    bringup_dir = get_package_share_directory('robot_bringup')
    tb3_gazebo_dir = get_package_share_directory('turtlebot3_gazebo')
    ros_gz_sim_dir = get_package_share_directory('ros_gz_sim')
    slam_params_file = os.path.join(bringup_dir, 'config', 'slam_params.yaml')
    nav2_params_file = os.path.join(bringup_dir, 'config', 'nav2_params.yaml')
    world_file = os.path.join(tb3_gazebo_dir, 'worlds', 'turtlebot3_house.world')
    rviz_config = os.path.join(
        get_package_share_directory('nav2_bringup'), 'rviz', 'nav2_default_view.rviz',
    )

    gz_server = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(ros_gz_sim_dir, 'launch', 'gz_sim.launch.py'),
        ),
        launch_arguments={
            'gz_args': ['-r -s -v2 ', world_file],
            'on_exit_shutdown': 'true',
        }.items(),
    )

    gz_gui = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(ros_gz_sim_dir, 'launch', 'gz_sim.launch.py'),
        ),
        launch_arguments={'gz_args': '-g -v2 '}.items(),
        condition=IfCondition(use_gz_gui),
    )

    robot_state_publisher = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(tb3_gazebo_dir, 'launch', 'robot_state_publisher.launch.py'),
        ),
        launch_arguments={'use_sim_time': use_sim_time}.items(),
    )

    # Own copy of the waffle model (robot_bringup/models/turtlebot3_waffle)
    # instead of turtlebot3_gazebo's stock spawn_turtlebot3.launch.py: the
    # stock model.sdf hardcodes a 1920x1080 camera (6.2MB/frame, ~55MB/s),
    # which a BEST_EFFORT subscriber sharing a busy executor (Nav2 + TF +
    # Ollama + ChromaDB clients) silently drops at the DDS layer — perceive
    # never got a frame. Our copy is patched to 640x480. See its model.sdf
    # for the full note. Never edit /opt/ros/jazzy/ directly (CLAUDE.md).
    waffle_model_path = os.path.join(
        bringup_dir, 'models', 'turtlebot3_waffle', 'model.sdf',
    )
    spawn_robot = Node(
        package='ros_gz_sim',
        executable='create',
        arguments=['-name', 'waffle', '-file', waffle_model_path, '-x', x_pose, '-y', y_pose, '-z', '0.01'],
        output='screen',
    )
    bridge_params = os.path.join(tb3_gazebo_dir, 'params', 'turtlebot3_waffle_bridge.yaml')
    ros_gz_bridge = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        arguments=['--ros-args', '-p', f'config_file:={bridge_params}'],
        output='screen',
    )
    ros_gz_image_bridge = Node(
        package='ros_gz_image',
        executable='image_bridge',
        arguments=['/camera/image_raw'],
        output='screen',
    )

    gz_resources = AppendEnvironmentVariable(
        'GZ_SIM_RESOURCE_PATH', os.path.join(tb3_gazebo_dir, 'models'),
    )

    slam_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(
                get_package_share_directory('slam_toolbox'),
                'launch',
                'online_async_launch.py',
            ),
        ),
        launch_arguments={
            'slam_params_file': slam_params_file,
            'use_sim_time': use_sim_time,
        }.items(),
    )

    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(
                get_package_share_directory('nav2_bringup'),
                'launch',
                'navigation_launch.py',
            ),
        ),
        launch_arguments={
            'use_sim_time': use_sim_time,
            'params_file': nav2_params_file,
        }.items(),
        condition=IfCondition(use_nav2),
    )

    rviz = Node(
        package='rviz2',
        executable='rviz2',
        arguments=['-d', rviz_config],
        parameters=[{'use_sim_time': True}],
        output='log',
        condition=IfCondition(use_rviz),
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            'use_sim_time', default_value='true',
            description='Use Gazebo simulation clock',
        ),
        DeclareLaunchArgument(
            'use_nav2', default_value='true',
            description='Start Nav2 alongside SLAM (disable for mapping-only runs)',
        ),
        DeclareLaunchArgument(
            'use_rviz', default_value='true',
            description='Open RViz with map + lidar + costmaps view',
        ),
        DeclareLaunchArgument(
            'use_gz_gui', default_value='false',
            description='Open the Gazebo GUI (drops sim real-time factor to ~0.15 on WSL2)',
        ),
        gz_resources,
        gz_server,
        gz_gui,
        robot_state_publisher,
        spawn_robot,
        ros_gz_bridge,
        ros_gz_image_bridge,
        slam_launch,
        nav2_launch,
        rviz,
    ])
