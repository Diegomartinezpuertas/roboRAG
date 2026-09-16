"""ROS 2 node that dispatches ExecuteSkill requests to the robot's skills."""

import json
import math
import os
import threading
import time
from pathlib import Path

import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, LaserScan
from std_msgs.msg import String
from tf2_ros import LookupException, TransformException
from tf2_ros.buffer import Buffer
from tf2_ros.transform_listener import TransformListener

from robot_interfaces.msg import SemanticObject
from robot_interfaces.srv import ExecuteSkill, UpdateMap
from robot_rag.map_session import MapSession, read_active_map_id
from robot_rag.scene_merge import scene_group_key
from robot_zones.room_semantics import classify_room, scene_context
from robot_zones.zone_store import ZoneStore

from robot_skills.explore_skill import find_best_frontier, find_nearest_frontier
from robot_skills.nav_skill import NavSkill
from robot_skills.perceive_skill import PerceiveSkill
from robot_skills.report_skill import ReportSkill
from robot_skills.scene_descriptor import describe_scan_360

# scan_360 sweeps in this many equal steps (a LIDAR scan already covers 360
# degrees in one reading; the steps are for the camera's narrow FOV).
SCAN_360_STEPS = 8

# Workspace root for the default data paths. Reads ROBOT_WS (exported by
# setup_env.sh) so the package is not tied to one developer's home directory;
# every path is still overridable as a ROS 2 parameter.
WS_ROOT = Path(os.environ.get('ROBOT_WS', Path.home() / 'robot_ws'))


class SkillsExecutorNode(Node):
    """ROS 2 node that executes robot skills dispatched via ExecuteSkill.srv.

    Subscribes:
        /camera/image_raw (sensor_msgs/Image): Latest camera frame, cached for perceive.
        /scan (sensor_msgs/LaserScan): Latest LIDAR scan, cached for perceive.
        /map (nav_msgs/OccupancyGrid): Latest SLAM occupancy grid, cached for explore.

    Looks up the map -> base_link TF (published by SLAM Toolbox) to get the
    robot's current pose, rather than subscribing to AMCL, which is not part
    of this project's localization stack.

    Publishes:
        /robot/response (std_msgs/String): Final natural language response.

    Services (server):
        /skills/execute (ExecuteSkill): Dispatches to
            navigate/explore/perceive/scan_360/report.

    Services (client):
        /rag/update_map (UpdateMap): Stores scene descriptions in semantic memory.

    Parameters:
        logs_dir (str): Directory for task history logs.
        zones_db (str): SQLite file with user-defined navigation zones.
        explore_strategy (str): How explore picks its next frontier: "clusters"
            (largest unexplored edge per metre, ADR-027) or "nearest" (the original
            nearest-cell rule, kept for comparison). Default: clusters
        maps_dir (str): Directory holding the map session (see ADR-019); its
            active id is stamped into each task log so the memory it becomes
            is scoped to the map the task actually ran on.
    """

    def __init__(self) -> None:
        super().__init__('skills_executor_node')

        self.declare_parameter('logs_dir', str(WS_ROOT / 'data' / 'logs'))
        self.declare_parameter('zones_db', str(WS_ROOT / 'data' / 'zones.db'))
        self.declare_parameter('maps_dir', str(WS_ROOT / 'data' / 'maps'))
        self.declare_parameter('explore_strategy', 'clusters')

        logs_dir = self.get_parameter('logs_dir').value
        self._maps_dir = self.get_parameter('maps_dir').value
        self._zones = ZoneStore(self.get_parameter('zones_db').value)

        self._nav_skill = NavSkill()
        self._perceive_skill = PerceiveSkill()
        response_pub = self.create_publisher(String, '/robot/response', 10)
        self._report_skill = ReportSkill(response_pub, logs_dir)

        # Sensor subscriptions and the map-update client go in a reentrant
        # group so a MultiThreadedExecutor keeps feeding them while a skill
        # (default group, serialized) is executing — explore needs fresh /map
        # mid-skill, and the update-map response must arrive while perceive
        # blocks on it.
        self._io_group = ReentrantCallbackGroup()
        self._update_map_client = self.create_client(
            UpdateMap, '/rag/update_map', callback_group=self._io_group,
        )
        # Client for persisting the SLAM map (save_map maintenance skill,
        # ADR-019). Created lazily-typed: the service type is imported inside
        # _run_save_map so this module stays importable without slam_toolbox.
        self._serialize_map_client = None

        self._latest_image: Image | None = None
        self._latest_scan: LaserScan | None = None
        self._latest_map: OccupancyGrid | None = None
        # spin_thread stays False: with True, tf2_ros adds THIS node to its own
        # SingleThreadedExecutor thread, competing with our executor for the
        # node's entities (it silenced the camera subscription). tf2_ros
        # already puts its subs in a ReentrantCallbackGroup, so our
        # MultiThreadedExecutor keeps TF flowing during long skill callbacks.
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        self.create_subscription(
            Image, '/camera/image_raw', self._on_image, qos_profile_sensor_data,
            callback_group=self._io_group,
        )
        self.create_subscription(
            LaserScan, '/scan', self._on_scan, qos_profile_sensor_data,
            callback_group=self._io_group,
        )
        self.create_subscription(
            OccupancyGrid, '/map', self._on_map, 1, callback_group=self._io_group,
        )

        self._execute_srv = self.create_service(
            ExecuteSkill, '/skills/execute', self._handle_execute,
        )
        self.get_logger().info('skills_executor_node ready')

    def _on_image(self, msg: Image) -> None:
        self._latest_image = msg

    def _on_scan(self, msg: LaserScan) -> None:
        self._latest_scan = msg

    def _on_map(self, msg: OccupancyGrid) -> None:
        self._latest_map = msg

    def _get_robot_pose(self) -> tuple[float, float]:
        """Looks up the robot's (x, y) in the map frame via TF, defaulting to (0, 0)."""
        try:
            transform = self._tf_buffer.lookup_transform('map', 'base_link', rclpy.time.Time())
        except (LookupException, TransformException):
            return (0.0, 0.0)
        translation = transform.transform.translation
        return (translation.x, translation.y)

    def _handle_execute(self, request: ExecuteSkill.Request, response: ExecuteSkill.Response):
        try:
            params = json.loads(request.params_json) if request.params_json else {}
        except json.JSONDecodeError as exc:
            response.success = False
            response.error_msg = f'Invalid params_json: {exc}'
            response.result_json = '{}'
            return response

        try:
            result = self._dispatch(request.skill_name, params)
        except Exception as exc:
            self.get_logger().error(f'Skill "{request.skill_name}" failed: {exc}')
            response.success = False
            response.error_msg = str(exc)
            response.result_json = '{}'
            return response

        response.success = True
        response.error_msg = ''
        response.result_json = json.dumps(result)
        return response

    def _resolve_zone(self, params: dict) -> dict:
        """Replaces a "zone" param with the explicit x/y center of that zone.

        Zones are exclusively the user-defined ones in the SQLite store; there
        are no hardcoded fallback coordinates (those were fictional and would
        send the robot to the wrong place — see ADR-011).

        Args:
            params: Navigate params, possibly {"zone": name}.

        Returns:
            Params with x/y resolved.

        Raises:
            ValueError: If the zone is unknown.
        """
        if 'zone' not in params:
            return params
        zone = params['zone']
        zones = self._zones.load_all()
        if zone not in zones:
            raise ValueError(f'Unknown zone: {zone}. Known zones: {sorted(zones)}')
        area = zones[zone]
        return {
            'x': (area['x_min'] + area['x_max']) / 2.0,
            'y': (area['y_min'] + area['y_max']) / 2.0,
        }

    def _dispatch(self, skill_name: str, params: dict) -> dict:
        if skill_name == 'navigate':
            # Resolve the zone BEFORE blocking on the Nav2 lifecycle. An unknown
            # zone is a pure validation error: failing fast returns an
            # actionable message ("Unknown zone: X. Known zones: [...]") to the
            # planner, whereas validating afterwards would hang until Nav2
            # happened to come up — indefinitely, if it never does.
            resolved = self._resolve_zone(params)
            self._nav_skill.wait_until_active()
            return self._nav_skill.navigate(resolved)
        if skill_name == 'explore':
            return self._run_explore(params)
        if skill_name == 'perceive':
            return self._run_perceive(params)
        if skill_name == 'scan_360':
            return self._run_scan_360(params)
        if skill_name == 'report':
            return self._run_report(params)
        if skill_name == 'save_map':
            # Maintenance skill, deliberately absent from the planner's prompt
            # and from toolkit.VALID_SKILLS, so the LLM never emits it. Reached
            # only by a direct `ros2 service call /skills/execute` (ADR-019).
            return self._run_save_map(params)
        raise ValueError(f'Unknown skill: {skill_name}')

    def _wait_for_future(self, future, timeout_sec: float) -> None:
        # Event-based wait: the client lives in the reentrant io group, so a
        # MultiThreadedExecutor thread delivers the response while this skill
        # callback blocks. Never spin the node on a throwaway executor — that
        # steals entity ownership from the main executor and the node goes
        # deaf once the callback returns.
        done = threading.Event()
        future.add_done_callback(lambda _: done.set())
        if not done.wait(timeout=timeout_sec):
            future.cancel()

    def _run_explore(self, params: dict) -> dict:
        duration_sec = float(params.get('duration_sec', 30.0))
        bounds = None
        if 'zone' in params:
            zones = self._zones.load_all()
            zone = params['zone']
            if zone not in zones:
                raise ValueError(f'Unknown zone: {zone}. Known zones: {sorted(zones)}')
            area = zones[zone]
            bounds = (area['x_min'], area['y_min'], area['x_max'], area['y_max'])
            robot_x, robot_y = self._get_robot_pose()
            if not (bounds[0] <= robot_x <= bounds[2] and bounds[1] <= robot_y <= bounds[3]):
                self.get_logger().info(f'Explore: moving into zone "{zone}" first')
                self._nav_skill.wait_until_active()
                self._nav_skill.navigate({
                    'x': (bounds[0] + bounds[2]) / 2.0,
                    'y': (bounds[1] + bounds[3]) / 2.0,
                })

        deadline = time.monotonic() + duration_sec
        visited = 0
        attempted: list[tuple[float, float]] = []
        while time.monotonic() < deadline:
            # Give SLAM a moment to publish the map grown by the previous
            # leg; the /map subscription updates concurrently (io group).
            time.sleep(1.0)
            if self._latest_map is None:
                continue
            robot_x, robot_y = self._get_robot_pose()
            # Largest unexplored edge per metre, not the nearest cell: the
            # nearest rule crept along one wall (ADR-027). Re-read per step so
            # the two can be compared live with `ros2 param set`.
            nearest = self.get_parameter('explore_strategy').value == 'nearest'
            frontier = (find_nearest_frontier if nearest else find_best_frontier)(
                self._latest_map, robot_x, robot_y, excluded=attempted, bounds=bounds,
            )
            if frontier is None:
                self.get_logger().info('Explore: no frontiers left in the target area')
                break
            attempted.append(frontier)
            self.get_logger().info(
                f'Explore: heading to frontier ({frontier[0]:.2f}, {frontier[1]:.2f})',
            )
            self._nav_skill.wait_until_active()
            result = self._nav_skill.navigate({'x': frontier[0], 'y': frontier[1]})
            if result['reached']:
                visited += 1
                # Self-building memory: describe every place the robot reaches
                # while exploring, so descriptive goals ("go to the white,
                # open room") become resolvable later without any manual
                # seeding. Failures must never abort the exploration itself.
                try:
                    self._perceive_and_store()
                except Exception as exc:  # noqa: BLE001 - best-effort side task
                    self.get_logger().warning(f'Explore: scene store failed: {exc}')
        return {'visited_frontiers': visited, 'message': f'Explored {visited} frontier(s)'}

    def _run_perceive(self, params: dict) -> dict:
        return self._perceive_and_store(zone=params.get('zone', ''))

    def _perceive_and_store(self, zone: str = '') -> dict:
        """Describes the surroundings from a single frame and stores the result."""
        result = self._perceive_skill.describe(self._latest_image, self._latest_scan)
        return self._store_scene_result(result, zone)

    def _run_scan_360(self, params: dict) -> dict:
        """Rotates in place, describes the full surroundings, and stores it.

        A 2D LIDAR already sees 360 degrees in one scan, so only the camera —
        whose field of view is narrow — needs the rotation: it samples
        dominant colors at each heading and merges them into one panoramic
        description (scene_descriptor.describe_scan_360), keyed to the
        robot's current position like a regular perceive.
        """
        steps = max(4, min(int(params.get('steps', SCAN_360_STEPS)), 16))
        angle = 2 * math.pi / steps

        self._nav_skill.wait_until_active()
        # Sample the current heading before turning: Nav2's Spin refuses to
        # move if an obstacle is too close (safety behavior, not a bug), and
        # without this the very first refusal would lose colors entirely
        # instead of degrading to a single-frame read like perceive.
        color_samples = [self._perceive_skill.sample_colors(self._latest_image)]
        completed = 0
        for _ in range(steps):
            if not self._nav_skill.spin(angle):
                break
            completed += 1
            time.sleep(0.3)  # let the camera settle after the turn
            color_samples.append(self._perceive_skill.sample_colors(self._latest_image))

        ranges = list(self._latest_scan.ranges) if self._latest_scan is not None else None
        result = describe_scan_360(color_samples, ranges)
        result['headings_completed'] = completed
        result['message'] = (
            f'Completed the full turn ({steps} steps).' if completed == steps else
            f'Turned {completed}/{steps} steps before stopping '
            f'(likely an obstacle too close to continue safely).'
        )
        return self._store_scene_result(result, params.get('zone', ''))

    def _store_scene_result(self, result: dict, zone: str = '') -> dict:
        """Upserts a scene-description result (perceive or scan_360) into memory.

        The observation carries a group key built from what the descriptor
        measured (the set of dominant colors plus the clutter class), so
        rag_node can fold a re-observation of the same look, in the same zone
        and map session, into the memory already stored nearby instead of
        storing the place again (ADR-025). The proposed object_id — the pose on
        a 0.5 m grid — is only used when the observation is genuinely new;
        `object_id` in the result is whichever id the memory ended up under.

        What the sensors measure is colors and clutter — nothing in that says
        "kitchen". So when the spot falls inside a zone whose name denotes a
        kind of room, that room's purpose is appended to the stored text (in
        both languages) and used as the object's label: the observation then
        answers "where does one usually cook?" as well as "where is the white
        open room?" (ADR-022).
        """
        pose_x, pose_y = self._get_robot_pose()
        zone = zone or self._zone_at(pose_x, pose_y)
        object_id = f'scene-{round(pose_x * 2) / 2:.1f}-{round(pose_y * 2) / 2:.1f}'
        room = classify_room(zone)
        description = result['description']
        context = scene_context(zone)
        if context:
            description = f'{description} {context}'
        # Reported back to the planner (and from there to the user): "I saw
        # this, and it was in the kitchen" is a better answer than "I saw this".
        result['zone'] = zone
        stored = self._update_map(
            object_id, room.key if room is not None else 'area',
            description, zone, pose_x, pose_y,
            group_key=scene_group_key(result.get('colors', []), result.get('clutter', 'unknown')),
        )
        result['stored'] = stored['success']
        result['object_id'] = stored['object_id'] or object_id
        result['merged'] = stored['merged']
        return result

    def _zone_at(self, x: float, y: float) -> str:
        """Returns the name of the user zone containing (x, y), or ''."""
        for name, a in self._zones.load_all().items():
            if a['x_min'] <= x <= a['x_max'] and a['y_min'] <= y <= a['y_max']:
                return name
        return ''

    def _update_map(
        self, object_id: str, label: str, description: str, zone: str,
        pose_x: float, pose_y: float, group_key: str = '',
    ) -> dict:
        """Stores one observation through /rag/update_map.

        Returns:
            Dict with "success", the "object_id" it was stored under (empty on
            failure) and whether it was "merged" into an existing memory.
        """
        failed = {'success': False, 'object_id': '', 'merged': False}
        if not self._update_map_client.wait_for_service(timeout_sec=2.0):
            self.get_logger().warning('/rag/update_map unavailable, skipping semantic map update')
            return failed

        semantic_object = SemanticObject()
        semantic_object.object_id = object_id
        semantic_object.label = label
        semantic_object.confidence = 1.0
        semantic_object.pose.position.x = pose_x
        semantic_object.pose.position.y = pose_y
        semantic_object.description = description
        semantic_object.room_zone = zone
        semantic_object.timestamp = self.get_clock().now().to_msg()
        semantic_object.group_key = group_key

        request = UpdateMap.Request(object_data=semantic_object)
        future = self._update_map_client.call_async(request)
        self._wait_for_future(future, timeout_sec=5.0)
        response = future.result()
        if not (response and response.success):
            return failed
        return {'success': True, 'object_id': response.object_id, 'merged': response.merged}

    def _run_report(self, params: dict) -> dict:
        task_context = {
            'task_id': str(self.get_clock().now().nanoseconds),
            'goal_text': params.get('goal_text', ''),
            # Stamp the map session so this log, once ingested into
            # task_history, is scoped to the map it ran on (ADR-019).
            'map_id': read_active_map_id(self._maps_dir),
        }
        return self._report_skill.report(params, task_context)

    def _run_save_map(self, params: dict) -> dict:
        """Serializes the current SLAM map so it (and its memories) survive a restart.

        Writes `<maps_dir>/<map_id>/map.{posegraph,data}` via SLAM Toolbox's
        serialize service, under the active map-session id. Relaunching with
        `saved_map:=<map_id>` deserializes it and re-pins the session, so the
        coordinate memories tagged with that id become valid again (ADR-019).

        Args:
            params: Optional {"name": str} to store under a stable name instead
                of the current session id.

        Returns:
            Dict with the map_id, the file path, and a message.

        Raises:
            RuntimeError: If SLAM Toolbox's serialize service is unavailable.
        """
        from slam_toolbox.srv import SerializePoseGraph

        if self._serialize_map_client is None:
            self._serialize_map_client = self.create_client(
                SerializePoseGraph, '/slam_toolbox/serialize_map',
                callback_group=self._io_group,
            )
        if not self._serialize_map_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('/slam_toolbox/serialize_map unavailable (is SLAM running?)')

        map_id = params.get('name') or read_active_map_id(self._maps_dir) or 'default'
        map_dir = Path(self._maps_dir) / map_id
        map_dir.mkdir(parents=True, exist_ok=True)
        filename = str(map_dir / 'map')

        future = self._serialize_map_client.call_async(
            SerializePoseGraph.Request(filename=filename),
        )
        self._wait_for_future(future, timeout_sec=30.0)
        response = future.result()
        if response is None:
            raise RuntimeError('serialize_map timed out')
        # If a name was given, pin the session to it so the saved map and its
        # memories share one id. If not, the map is saved under the id already
        # active, so nothing to re-pin.
        if params.get('name'):
            MapSession(self._maps_dir).set_id(map_id)
        self.get_logger().info(f'Saved SLAM map "{map_id}" to {filename}.posegraph')
        return {
            'map_id': map_id,
            'path': f'{filename}.posegraph',
            'message': f'Saved map "{map_id}". Reload with saved_map:={map_id}.',
        }


def main(args: list[str] | None = None) -> None:
    """Entry point for the skills_executor_node executable."""
    rclpy.init(args=args)
    node = SkillsExecutorNode()
    # Dedicated MultiThreadedExecutor: skill callbacks (default group) run
    # serialized while sensor subs and client responses (io group) keep
    # flowing on other threads. The process-global executor stays free for
    # nav2_simple_commander's BasicNavigator, which spins itself on it
    # internally via rclpy.spin_until_future_complete.
    executor = rclpy.executors.MultiThreadedExecutor(num_threads=6)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        # ExternalShutdownException is how rclpy reports SIGINT/SIGTERM from
        # `ros2 launch` shutting the stack down — an ordinary stop, not a crash.
        pass
    finally:
        node.destroy_node()
        # Guarded: on external shutdown the context is already down and an
        # unconditional shutdown() raises RCLError over the real exit.
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
