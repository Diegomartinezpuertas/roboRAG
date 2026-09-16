"""Launches Gazebo (headless) + TurtleBot3 Waffle + SLAM Toolbox + Nav2 + RViz.

The Gazebo GUI is disabled by default: under WSL2 it renders on llvmpipe and
drags the real-time factor down to ~0.15. RViz (use_rviz, default true) is the
intended visualization — map, lidar scan, costmaps — and keeps RTF at ~1.0.
Pass use_gz_gui:=true to get the Gazebo window back.

saved_map:=<id> starts SLAM from a saved pose graph instead of an empty map —
one saved from the dashboard (data/maps/<id>) or shipped with the repository
(robot_bringup/maps/<id>). See ADR-019 and ADR-026.
"""

import os
import tempfile

import yaml

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    AppendEnvironmentVariable,
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    LogInfo,
    OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import AndSubstitution, LaunchConfiguration, NotSubstitution
from launch_ros.actions import Node

from robot_bringup.saved_maps import resolve_saved_map, slam_params_for_saved_map

WS_ROOT = os.environ.get('ROBOT_WS', os.path.join(os.path.expanduser('~'), 'robot_ws'))


def _slam(context, slam_params_file: str, use_sim_time) -> list:
    """Includes SLAM Toolbox, starting from a saved map when saved_map is set.

    SLAM Toolbox reads the map to load from its parameters file, and the
    stock launch file takes only that file — so for a saved map the project's
    parameters are rewritten into a temporary copy with map_file_name and a
    dock start (robot_bringup.saved_maps). An unknown id fails the launch with
    the paths searched, rather than silently mapping from scratch.
    """
    map_id = LaunchConfiguration('saved_map').perform(context).strip()
    params_file = slam_params_file
    actions = []
    if map_id:
        map_base = resolve_saved_map(
            map_id, WS_ROOT, get_package_share_directory('robot_bringup'),
        )
        with open(slam_params_file, encoding='utf-8') as source:
            params = slam_params_for_saved_map(yaml.safe_load(source), map_base)
        with tempfile.NamedTemporaryFile(
            'w', suffix='.yaml', prefix=f'slam_params_{map_id}_', delete=False,
        ) as rewritten:
            yaml.safe_dump(params, rewritten)
            params_file = rewritten.name
        actions.append(LogInfo(msg=f'SLAM starts from saved map "{map_id}": {map_base}'))
    actions.append(IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(
                get_package_share_directory('slam_toolbox'), 'launch', 'online_async_launch.py',
            ),
        ),
        launch_arguments={
            'slam_params_file': params_file,
            'use_sim_time': use_sim_time,
        }.items(),
    ))
    return actions


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
    # Without Nav2 (a manual mapping run) the stock view's Nav2 panels poll for
    # servers that never come up and flood the terminal; this copy drops them.
    rviz_mapping_config = os.path.join(bringup_dir, 'rviz', 'mapping.rviz')

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

    slam_launch = OpaqueFunction(
        function=_slam, args=[slam_params_file, use_sim_time],
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
        condition=IfCondition(AndSubstitution(use_rviz, use_nav2)),
    )
    rviz_mapping = Node(
        package='rviz2',
        executable='rviz2',
        arguments=['-d', rviz_mapping_config],
        parameters=[{'use_sim_time': True}],
        output='log',
        condition=IfCondition(AndSubstitution(use_rviz, NotSubstitution(use_nav2))),
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
        DeclareLaunchArgument(
            'saved_map', default_value='',
            description='Start SLAM from a saved map id (data/maps/<id> or robot_bringup/maps/<id>); '
                        'empty maps from scratch',
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
        rviz_mapping,
    ])
