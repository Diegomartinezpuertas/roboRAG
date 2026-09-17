"""Launches Gazebo (headless) + TurtleBot3 Waffle + SLAM Toolbox + Nav2 + RViz.

The Gazebo GUI is disabled by default: under WSL2 it renders on llvmpipe and
costs real-time factor — ~0.15 on the setup this was first measured on; on the
current one the cost is small (measured 2026-09-16: ~0.68 headless, ~0.59 with
the window open). RViz (use_rviz, default true) is the intended visualization —
map, lidar scan, costmaps. Pass use_gz_gui:=true to get the Gazebo window back.

saved_map:=<id> starts SLAM from a saved pose graph instead of an empty map —
one saved from the dashboard (data/maps/<id>) or shipped with the repository
(robot_bringup/maps/<id>). See ADR-019 and ADR-026. By default SLAM Toolbox
continues the saved pose graph, so the map follows what the robot sees;
saved_map_mode:=localization instead serves the saved occupancy image through
map_server and localizes with AMCL, leaving the map untouched (ADR-035).

The launch fails at once if a Gazebo server is already running in the same
partition — usually one left behind by a closed terminal. Two simulations feed
two robots into one SLAM and corrupt the map (ADR-034).
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

from robot_bringup.saved_maps import (
    DEFAULT_SAVED_MAP_MODE,
    DEFAULT_SPAWN_X,
    DEFAULT_SPAWN_Y,
    DEFAULT_WORLD,
    nav2_params_for_start_pose,
    resolve_map_image,
    resolve_saved_map,
    slam_params_for_saved_map,
    start_pose_in_zone,
    uses_amcl,
    world_pose_for_map_point,
)
from robot_zones.zone_store import ZoneStore
from robot_bringup.sim_guard import (
    gazebo_partition,
    running_gazebo_servers,
    second_simulation_error,
)

WS_ROOT = os.environ.get('ROBOT_WS', os.path.join(os.path.expanduser('~'), 'robot_ws'))


def _refuse_second_simulation(context) -> list:
    """Fails the launch while another Gazebo server runs in this partition (ADR-034).

    It runs before this launch starts its own server, so any server found
    belongs to another simulation.
    """
    servers = running_gazebo_servers(gazebo_partition(os.environ))
    if servers:
        raise RuntimeError(second_simulation_error(servers))
    return []


def _start_pose(context) -> tuple[float, float] | None:
    """The map-frame point this launch starts the robot at, or None for the map's origin.

    `start_zone:=<name>` reads the zone's centre from the shared zone store, so
    a run on a saved map can begin in a room instead of wherever mapping began
    (ADR-035). An unknown name fails the launch with the stored names.
    """
    zone = LaunchConfiguration('start_zone', default='').perform(context).strip()
    if not zone:
        return None
    zones = ZoneStore(os.path.join(WS_ROOT, 'data', 'zones.db')).load_all()
    return start_pose_in_zone(zone, zones)


def _rewritten(params: dict, prefix: str) -> str:
    """Writes parameters to a temporary YAML file and returns its path."""
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', prefix=prefix, delete=False) as handle:
        yaml.safe_dump(params, handle)
        return handle.name


def _robot(context, model_path: str) -> list:
    """Spawns the waffle where this launch starts: the start zone, else x_pose/y_pose."""
    start = _start_pose(context)
    if start is None:
        x_pose = LaunchConfiguration('x_pose', default=str(DEFAULT_SPAWN_X)).perform(context)
        y_pose = LaunchConfiguration('y_pose', default=str(DEFAULT_SPAWN_Y)).perform(context)
    else:
        world_x, world_y = world_pose_for_map_point(*start)
        x_pose, y_pose = f'{world_x:.3f}', f'{world_y:.3f}'
    return [
        LogInfo(msg=f'Spawning the robot at world ({x_pose}, {y_pose})'),
        Node(
            package='ros_gz_sim',
            executable='create',
            arguments=['-name', 'waffle', '-file', model_path,
                       '-x', x_pose, '-y', y_pose, '-z', '0.01'],
            output='screen',
        ),
    ]


def _slam(context, slam_params_file: str, nav2_params_file: str, use_sim_time) -> list:
    """Includes what provides the map and localization: SLAM Toolbox, or map_server + AMCL.

    - No saved map: SLAM Toolbox maps from scratch.
    - A saved map, saved_map_mode:=mapping (default): SLAM Toolbox continues the
      pose graph. It reads the map to load from its parameters file, and the
      stock launch file takes only that file — so the project's parameters are
      rewritten into a temporary copy with map_file_name and a dock start.
    - A saved map, saved_map_mode:=localization: nav2_bringup's localization
      launch — map_server serves the saved occupancy image and AMCL localizes
      from the map origin (nav2_params.yaml). The map cannot change.

    An unknown id, a map without its occupancy image, or an unknown mode fails
    the launch with the reason, rather than silently mapping from scratch.
    """
    map_id = LaunchConfiguration('saved_map').perform(context).strip()
    mode = LaunchConfiguration(
        'saved_map_mode', default=DEFAULT_SAVED_MAP_MODE,
    ).perform(context).strip()
    start_pose = _start_pose(context)
    params_file = slam_params_file
    actions = []
    if map_id:
        map_base = resolve_saved_map(
            map_id, WS_ROOT, get_package_share_directory('robot_bringup'),
        )
        if uses_amcl(map_id, mode):
            map_yaml = resolve_map_image(map_base)
            localization_params = nav2_params_file
            if start_pose is not None:
                with open(nav2_params_file, encoding='utf-8') as source:
                    localization_params = _rewritten(
                        nav2_params_for_start_pose(yaml.safe_load(source), start_pose),
                        f'nav2_params_{map_id}_',
                    )
            return [
                LogInfo(msg=f'Saved map "{map_id}" loads read-only (map_server + AMCL): {map_yaml}'),
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(
                        os.path.join(
                            get_package_share_directory('nav2_bringup'),
                            'launch', 'localization_launch.py',
                        ),
                    ),
                    launch_arguments={
                        'map': str(map_yaml),
                        'params_file': localization_params,
                        'use_sim_time': use_sim_time,
                        'autostart': 'true',
                        'use_composition': 'False',
                    }.items(),
                ),
            ]
        with open(slam_params_file, encoding='utf-8') as source:
            params = slam_params_for_saved_map(yaml.safe_load(source), map_base, start_pose)
        params_file = _rewritten(params, f'slam_params_{map_id}_')
        where = 'at the dock' if start_pose is None \
            else f'at ({start_pose[0]:.2f}, {start_pose[1]:.2f})'
        actions.append(
            LogInfo(msg=f'SLAM continues saved map "{map_id}" (mapping), starting {where}'),
        )
    else:
        uses_amcl(map_id, mode)   # a typo in saved_map_mode fails here too
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
    bringup_dir = get_package_share_directory('robot_bringup')
    tb3_gazebo_dir = get_package_share_directory('turtlebot3_gazebo')
    ros_gz_sim_dir = get_package_share_directory('ros_gz_sim')
    slam_params_file = os.path.join(bringup_dir, 'config', 'slam_params.yaml')
    nav2_params_file = os.path.join(bringup_dir, 'config', 'nav2_params.yaml')
    world_file = os.path.join(tb3_gazebo_dir, 'worlds', f'{DEFAULT_WORLD}.world')
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
    spawn_robot = OpaqueFunction(function=_robot, args=[waffle_model_path])
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
        function=_slam, args=[slam_params_file, nav2_params_file, use_sim_time],
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

    # The single owner of /cmd_vel: manual driving (/robot/cmd_vel_manual) outranks
    # Nav2 (/cmd_vel_nav_out). Runs with or without Nav2 (ADR-029).
    cmd_vel_mux = Node(
        package='robot_skills',
        executable='cmd_vel_mux_node',
        name='cmd_vel_mux_node',
        output='screen',
        parameters=[{'use_sim_time': use_sim_time}],
        respawn=True,
        respawn_delay=1.0,
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
            description='Open the Gazebo GUI (costs real-time factor on WSL2; ~0.68 -> ~0.59 measured)',
        ),
        DeclareLaunchArgument(
            'saved_map', default_value='',
            description='Start SLAM from a saved map id (data/maps/<id> or robot_bringup/maps/<id>); '
                        'empty maps from scratch',
        ),
        DeclareLaunchArgument(
            'start_zone', default_value='',
            description="Start at this zone's centre instead of where the map begins: "
                        'spawns the robot there and tells SLAM (map_start_pose) or AMCL',
        ),
        DeclareLaunchArgument(
            'saved_map_mode', default_value=DEFAULT_SAVED_MAP_MODE,
            description='With saved_map: mapping (SLAM Toolbox) keeps adding scans, so the map '
                        'follows what the robot sees; localization (map_server + AMCL) leaves it '
                        'exactly as saved',
        ),
        OpaqueFunction(function=_refuse_second_simulation),
        gz_resources,
        gz_server,
        gz_gui,
        robot_state_publisher,
        spawn_robot,
        ros_gz_bridge,
        ros_gz_image_bridge,
        slam_launch,
        cmd_vel_mux,
        nav2_launch,
        rviz,
        rviz_mapping,
    ])
