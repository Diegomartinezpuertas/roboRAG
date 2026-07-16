"""ROS 2 node that dispatches ExecuteSkill requests to the robot's skills."""

import json
import threading
import time

import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String
from tf2_ros import LookupException, TransformException
from tf2_ros.buffer import Buffer
from tf2_ros.transform_listener import TransformListener

from robot_interfaces.msg import SemanticObject
from robot_interfaces.srv import ExecuteSkill, UpdateMap
from robot_zones.zone_store import ZoneStore

from robot_skills.explore_skill import find_nearest_frontier
from robot_skills.nav_skill import NavSkill
from robot_skills.perceive_skill import PerceiveSkill
from robot_skills.report_skill import ReportSkill


class SkillsExecutorNode(Node):
    """ROS 2 node that executes robot skills dispatched via ExecuteSkill.srv.

    Subscribes:
        /camera/image_raw (sensor_msgs/Image): Latest camera frame, cached for perceive.
        /map (nav_msgs/OccupancyGrid): Latest SLAM occupancy grid, cached for explore.

    Looks up the map -> base_link TF (published by SLAM Toolbox) to get the
    robot's current pose, rather than subscribing to AMCL, which is not part
    of this project's localization stack.

    Publishes:
        /robot/response (std_msgs/String): Final natural language response.

    Services (server):
        /skills/execute (ExecuteSkill): Dispatches to navigate/explore/perceive/report.

    Services (client):
        /rag/update_map (UpdateMap): Stores objects found during perceive.

    Parameters:
        ollama_base_url (str): Ollama server URL. Default: http://localhost:11434
        vision_model (str): Vision model name. Default: qwen2.5vl:7b
        logs_dir (str): Directory for task history logs.
    """

    def __init__(self) -> None:
        super().__init__('skills_executor_node')

        self.declare_parameter('ollama_base_url', 'http://localhost:11434')
        self.declare_parameter('vision_model', 'qwen2.5vl:7b')
        self.declare_parameter('logs_dir', '/home/diego/robot_ws/data/logs')
        self.declare_parameter('zones_db', '/home/diego/robot_ws/data/zones.db')

        base_url = self.get_parameter('ollama_base_url').value
        vision_model = self.get_parameter('vision_model').value
        logs_dir = self.get_parameter('logs_dir').value
        self._zones = ZoneStore(self.get_parameter('zones_db').value)

        self._nav_skill = NavSkill()
        self._perceive_skill = PerceiveSkill(base_url, vision_model)
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

        self._latest_image: Image | None = None
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
            OccupancyGrid, '/map', self._on_map, 1, callback_group=self._io_group,
        )

        self._execute_srv = self.create_service(
            ExecuteSkill, '/skills/execute', self._handle_execute,
        )
        self.get_logger().info('skills_executor_node ready')

    def _on_image(self, msg: Image) -> None:
        self._latest_image = msg

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
            self._nav_skill.wait_until_active()
            return self._nav_skill.navigate(self._resolve_zone(params))
        if skill_name == 'explore':
            return self._run_explore(params)
        if skill_name == 'perceive':
            return self._run_perceive(params)
        if skill_name == 'report':
            return self._run_report(params)
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
            frontier = find_nearest_frontier(
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
        return {'visited_frontiers': visited, 'message': f'Explored {visited} frontier(s)'}

    def _run_perceive(self, params: dict) -> dict:
        if self._latest_image is None:
            raise RuntimeError('No camera frame received yet on /camera/image_raw')
        query = params.get('query', 'list all objects')
        zone = params.get('zone', '')
        result = self._perceive_skill.describe(self._latest_image, query)
        pose_x, pose_y = self._get_robot_pose()

        for index, obj in enumerate(result.get('objects', [])):
            object_id = f'{self.get_clock().now().nanoseconds}-{index}'
            obj['object_id'] = object_id
            self._update_map(object_id, obj, zone, pose_x, pose_y)
        return result

    def _update_map(
        self, object_id: str, obj: dict, zone: str, pose_x: float, pose_y: float,
    ) -> None:
        if not self._update_map_client.wait_for_service(timeout_sec=2.0):
            self.get_logger().warning('/rag/update_map unavailable, skipping semantic map update')
            return

        semantic_object = SemanticObject()
        semantic_object.object_id = object_id
        semantic_object.label = obj.get('label', 'unknown')
        semantic_object.confidence = float(obj.get('confidence', 0.0))
        semantic_object.pose.position.x = pose_x
        semantic_object.pose.position.y = pose_y
        semantic_object.description = obj.get('description', '')
        semantic_object.room_zone = zone
        semantic_object.timestamp = self.get_clock().now().to_msg()

        request = UpdateMap.Request(object_data=semantic_object)
        future = self._update_map_client.call_async(request)
        self._wait_for_future(future, timeout_sec=5.0)

    def _run_report(self, params: dict) -> dict:
        task_context = {
            'task_id': str(self.get_clock().now().nanoseconds),
            'goal_text': params.get('goal_text', ''),
        }
        return self._report_skill.report(params, task_context)


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
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
