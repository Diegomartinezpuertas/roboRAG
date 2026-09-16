"""ROS 2 node serving a web dashboard: observability, goals, zones, memory, manual driving."""

import json
import os
import threading
import time
from pathlib import Path

import uvicorn

import rclpy
from geometry_msgs.msg import Twist, TwistStamped
from nav_msgs.msg import OccupancyGrid
from rcl_interfaces.msg import Log
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.clock import Clock, ClockType
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import String
from tf2_ros import LookupException, TransformException
from tf2_ros.buffer import Buffer
from tf2_ros.transform_listener import TransformListener

from robot_interfaces.msg import SemanticObject
from robot_interfaces.srv import ExecuteSkill, InspectMemory, UpdateMap
from robot_zones.room_semantics import describe_zone, room_label
from robot_zones.zone_store import ZoneStore

from robot_dashboard.teleop import TeleopState, twist_from_keys
from robot_dashboard.web_api import build_app, render_map_png

# Workspace root for the default data paths. Reads ROBOT_WS (exported by
# setup_env.sh) so the package is not tied to one developer's home directory;
# the path is still overridable as a ROS 2 parameter.
WS_ROOT = Path(os.environ.get('ROBOT_WS', Path.home() / 'robot_ws'))

# Service timeouts for the calls the HTTP layer makes synchronously. They run
# on uvicorn's threads, never on the executor, so blocking here delays one
# browser request and nothing else — but a browser request must still end.
INSPECT_TIMEOUT_SEC = 15.0   # an embedding round-trip through Ollama, plus slack
SAVE_MAP_TIMEOUT_SEC = 45.0  # slam_toolbox serializing the pose graph to disk

MAP_QOS = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)


class EventBuffer:
    """Thread-safe ring buffer of dashboard events with monotonically increasing IDs.

    Args:
        max_events: Maximum number of events retained.
    """

    def __init__(self, max_events: int = 3000) -> None:
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._next_id = 1
        self._max_events = max_events

    def append(self, event_type: str, text: str, source: str = '', level: int = 20) -> None:
        """Appends an event, evicting the oldest entries beyond capacity.

        Args:
            event_type: One of "goal", "status", "response", "rosout".
            text: Event message text.
            source: Originating node name (for rosout events).
            level: rcl log severity (10=DEBUG ... 50=FATAL).
        """
        with self._lock:
            self._events.append({
                'id': self._next_id, 'ts': time.time(), 'type': event_type,
                'text': text, 'source': source, 'level': level,
            })
            self._next_id += 1
            if len(self._events) > self._max_events:
                del self._events[: len(self._events) - self._max_events]

    def since(self, last_id: int) -> list[dict]:
        """Returns all events with id greater than last_id."""
        with self._lock:
            return [e for e in self._events if e['id'] > last_id]


class DashboardNode(Node):
    """ROS 2 node exposing a web dashboard on localhost for the robot agent.

    Serves a single-page UI with the live planning timeline (/robot/goal,
    /robot/status, /robot/response), a filtered /rosout log viewer, an
    interactive SLAM map (robot pose, named zones, area selection), a goal
    input box with browser-based voice recognition, a browser of the robot's
    RAG memory, and WASD manual driving for mapping the house by hand.

    Subscribes:
        /robot/goal (std_msgs/String): Goals sent by any client, echoed to the timeline.
        /robot/status (std_msgs/String): Planner execution status.
        /robot/response (std_msgs/String): Final responses to the user.
        /rosout (rcl_interfaces/Log): Aggregated logs from every node.
        /map (nav_msgs/OccupancyGrid): SLAM map, rendered in the UI.

    Publishes:
        /robot/goal (std_msgs/String): Goals submitted through the web UI.
        /cmd_vel (geometry_msgs/TwistStamped): Manual driving commands, published
            only while someone is actually driving (see teleop.py).

    Services (client):
        /rag/update_map (UpdateMap): Indexes named zones into semantic memory — each
            one on save, and all of them once per start, into the active session.
        /rag/inspect (InspectMemory): Browses/searches memory for the UI's viewer.
        /skills/execute (ExecuteSkill): Saves the SLAM map after a manual mapping run.

    Parameters:
        http_host (str): Bind address for the HTTP server. Default: 127.0.0.1
            (loopback only — the API has no authentication; see
            config/agent_params.yaml to expose it on the network).
        http_port (int): Port for the HTTP server. Default: 8080
        zones_db (str): SQLite file where named zones are persisted.
        cmd_vel_topic (str): Topic for manual driving commands. Default: /cmd_vel
        cmd_vel_stamped (bool): Publish geometry_msgs/TwistStamped instead of Twist.
            Default: True — this stack's Gazebo bridge and Nav2 (enable_stamped_cmd_vel)
            both speak the stamped form; set False for a plain-Twist base.
        teleop_linear_speed (float): Forward/backward speed in m/s. Default: 0.18
        teleop_angular_speed (float): Turning speed in rad/s. Default: 1.0
        teleop_timeout_sec (float): Deadman window; a command not refreshed within
            it is replaced by a stop. Default: 0.6
        teleop_rate_hz (float): Rate at which held commands are republished. Default: 20.0
    """

    def __init__(self) -> None:
        super().__init__('dashboard_node')
        self.declare_parameter('http_host', '127.0.0.1')
        self.declare_parameter('http_port', 8080)
        self.declare_parameter('zones_db', str(WS_ROOT / 'data' / 'zones.db'))
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')
        self.declare_parameter('cmd_vel_stamped', True)
        # Comfortably below the TurtleBot3 Waffle's maxima (0.26 m/s,
        # 1.82 rad/s) — and low enough that even a boosted command (x1.4) stays
        # inside them. A human driving through doorways on a software-rendered
        # simulation needs the margin, and SLAM Toolbox's scan matching degrades
        # when the robot is spun faster than it can scan.
        self.declare_parameter('teleop_linear_speed', 0.18)
        self.declare_parameter('teleop_angular_speed', 1.0)
        self.declare_parameter('teleop_timeout_sec', 0.6)
        self.declare_parameter('teleop_rate_hz', 20.0)

        self.events = EventBuffer()
        self.zones = ZoneStore(self.get_parameter('zones_db').value)
        self._map_lock = threading.Lock()
        self._latest_map: OccupancyGrid | None = None
        self._map_png_b64: str | None = None
        self._map_png_stamp: int = -1

        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        self._goal_pub = self.create_publisher(String, '/robot/goal', 10)

        # Manual driving. The publisher's message type follows cmd_vel_stamped:
        # ros_gz_bridge maps geometry_msgs/TwistStamped onto the simulator's
        # gz.msgs.Twist here, and Nav2 runs with enable_stamped_cmd_vel, so a
        # plain Twist would be published into a topic nobody is listening to.
        self._teleop = TeleopState(self.get_parameter('teleop_timeout_sec').value)
        self._teleop_stamped = self.get_parameter('cmd_vel_stamped').value
        self._teleop_driving = False
        self._cmd_vel_pub = self.create_publisher(
            TwistStamped if self._teleop_stamped else Twist,
            self.get_parameter('cmd_vel_topic').value, 10,
        )
        # Its own callback group so the deadman keeps ticking on schedule even
        # while the map subscription or a service response occupies the others.
        # And explicitly on SYSTEM_TIME, not the node's clock: with
        # use_sim_time=true this timer would otherwise run on Gazebo's clock,
        # so a paused or slow simulation (RTF drops to ~0.15 with the Gazebo
        # GUI open) would stretch the interval a human and their browser
        # measure in wall seconds — and with no /clock at all it would never
        # fire. The deadman is a safety property of the person driving, so it
        # is timed like one; only the message stamp uses the node's clock.
        self._teleop_group = MutuallyExclusiveCallbackGroup()
        self.create_timer(
            1.0 / max(1.0, self.get_parameter('teleop_rate_hz').value),
            self._publish_teleop, callback_group=self._teleop_group,
            clock=Clock(clock_type=ClockType.SYSTEM_TIME),
        )
        # The map-update client lives in its own callback group so a
        # MultiThreadedExecutor thread delivers its response while the (light,
        # serialized) topic subscriptions run on the default group. The node is
        # driven from two sides — the executor and uvicorn's server thread —
        # so it follows the same executor discipline as the other nodes (ADR-007)
        # rather than the single-threaded spin it used to run on.
        self._client_group = MutuallyExclusiveCallbackGroup()
        self._update_map_client = self.create_client(
            UpdateMap, '/rag/update_map', callback_group=self._client_group,
        )
        # The memory viewer and the map save block a browser request until they
        # answer, so they get their own group: a slow embedding must not sit in
        # front of a zone indexing call (or vice versa) in one queue.
        self._request_group = MutuallyExclusiveCallbackGroup()
        self._inspect_client = self.create_client(
            InspectMemory, '/rag/inspect', callback_group=self._request_group,
        )
        self._skills_client = self.create_client(
            ExecuteSkill, '/skills/execute', callback_group=self._request_group,
        )
        # Zones live in SQLite with no session, but their memory documents are
        # tagged with the map session they were indexed under (ADR-019). Start
        # on another session — loading a saved map pins its id — and every zone
        # would silently drop out of retrieval, "ve donde se suele cocinar"
        # included. So once rag_node answers, every zone is indexed again into
        # the active session: an idempotent upsert by zone id (ADR-026).
        self._zones_reindexed = False
        self._reindex_timer = self.create_timer(
            3.0, self._reindex_zones, callback_group=self._client_group,
            clock=Clock(clock_type=ClockType.SYSTEM_TIME),
        )
        self.create_subscription(String, '/robot/goal', self._on_goal, 10)
        self.create_subscription(String, '/robot/status', self._on_status, 10)
        self.create_subscription(String, '/robot/response', self._on_response, 10)
        self.create_subscription(Log, '/rosout', self._on_rosout, 50)
        self.create_subscription(OccupancyGrid, '/map', self._on_map, MAP_QOS)

        host = self.get_parameter('http_host').value
        port = self.get_parameter('http_port').value
        self._server_thread = threading.Thread(
            target=self._run_http_server, args=(host, port), daemon=True,
        )
        self._server_thread.start()
        self.get_logger().info(f'dashboard_node ready — http://localhost:{port}')

    def publish_goal(self, text: str) -> None:
        """Publishes a natural language goal on /robot/goal.

        Args:
            text: Goal text to publish.
        """
        self._goal_pub.publish(String(data=text))

    def get_robot_pose(self) -> dict | None:
        """Returns the robot's map-frame pose {x, y} via TF, or None if unavailable."""
        try:
            transform = self._tf_buffer.lookup_transform('map', 'base_link', rclpy.time.Time())
        except (LookupException, TransformException):
            return None
        translation = transform.transform.translation
        return {'x': translation.x, 'y': translation.y}

    def get_map_snapshot(self) -> dict | None:
        """Returns the rendered map PNG (base64) plus geometry metadata.

        The PNG is re-rendered only when a newer grid has arrived, and the
        grid's stamp travels with it so the HTTP layer can version the image
        (ETag). Rows are flipped so the image's top edge corresponds to y_max.
        """
        with self._map_lock:
            grid = self._latest_map
            if grid is None:
                return None
            stamp = grid.header.stamp.sec * 10**9 + grid.header.stamp.nanosec
            if stamp != self._map_png_stamp:
                self._map_png_b64 = render_map_png(grid)
                self._map_png_stamp = stamp
            info = grid.info
            return {
                'png_b64': self._map_png_b64,
                'resolution': info.resolution,
                'origin_x': info.origin.position.x,
                'origin_y': info.origin.position.y,
                'width': info.width,
                'height': info.height,
                'stamp': stamp,
            }

    def index_zone_in_memory(self, name: str, area: dict) -> None:
        """Fire-and-forget: upserts a named zone into semantic memory via /rag/update_map.

        Called from the HTTP request handler, so it must never block it. The
        readiness check is instantaneous (`service_is_ready`, not a timed
        `wait_for_service`), and the request itself is dispatched with
        `call_async` whose result is not awaited — the zone is already safely
        in SQLite by the time this runs; indexing it in the RAG is best-effort.

        Args:
            name: Zone name, e.g. "cocina".
            area: {x_min, y_min, x_max, y_max} in map-frame meters.
        """
        if not self._update_map_client.service_is_ready():
            self.get_logger().warning('/rag/update_map unavailable, zone not indexed in memory')
            return
        obj = SemanticObject()
        obj.object_id = f'zone-{name}'
        # The label is the kind of room the name denotes ("kitchen"), falling
        # back to "zone" — it leads the stored document, so the English room
        # word is there even for a Spanish zone name.
        obj.label = room_label(name)
        obj.confidence = 1.0
        obj.pose.position.x = (area['x_min'] + area['x_max']) / 2.0
        obj.pose.position.y = (area['y_min'] + area['y_max']) / 2.0
        # A zone named after a room is described by what that room is *for*, in
        # both languages, so "ve donde se suele cocinar" retrieves the kitchen;
        # a name with no known meaning keeps the plain description (ADR-022).
        obj.description = describe_zone(name, area)
        obj.room_zone = name
        obj.timestamp = self.get_clock().now().to_msg()
        self._update_map_client.call_async(UpdateMap.Request(object_data=obj))

    def inspect_memory(
        self, collection: str, query: str, limit: int, active_map_only: bool,
    ) -> dict:
        """Browses or searches one RAG collection for the dashboard's memory viewer.

        Args:
            collection: "semantic_map" | "knowledge_base" | "task_history".
            query: Natural language query; empty browses the entries as stored.
            limit: Maximum number of entries to return.
            active_map_only: Scope coordinate collections to the active map
                session — i.e. show exactly what the planner would retrieve
                (ADR-019) — instead of everything ever stored.

        Returns:
            Dict with "ok" (bool), "items" (list of entries with id, document,
            metadata and score), "stats" ({collection: count}), "map_id" (the
            active map session) and "error" (empty when ok).
        """
        empty = {'ok': False, 'items': [], 'stats': {}, 'map_id': '', 'error': ''}
        if not self._inspect_client.service_is_ready():
            return {**empty, 'error': '/rag/inspect unavailable (is rag_node running?)'}
        request = InspectMemory.Request(
            collection_name=collection, query_text=query,
            limit=limit, active_map_only=active_map_only,
        )
        response = self._call_service(self._inspect_client, request, INSPECT_TIMEOUT_SEC)
        if response is None:
            return {**empty, 'error': 'the memory service did not answer in time'}
        return {
            'ok': response.success,
            'items': json.loads(response.items_json or '[]'),
            'stats': json.loads(response.stats_json or '{}'),
            'map_id': response.map_id,
            'error': response.error_msg,
        }

    def save_map(self, name: str = '') -> dict:
        """Persists the live SLAM map via the save_map maintenance skill (ADR-019).

        This is what makes a manual mapping run worth doing: the map the user
        just drove out, and the coordinate memories tagged with its session id,
        both survive the next launch.

        Args:
            name: Stable name to save under, e.g. "casa". Empty saves under the
                active map-session id.

        Returns:
            Dict with "ok" (bool), "result" (the skill's payload: map_id, path,
            message) and "error" (empty when ok).
        """
        if not self._skills_client.service_is_ready():
            return {
                'ok': False, 'result': {},
                'error': '/skills/execute unavailable (is the agent stack running?)',
            }
        request = ExecuteSkill.Request(
            skill_name='save_map',
            params_json=json.dumps({'name': name} if name else {}),
        )
        response = self._call_service(self._skills_client, request, SAVE_MAP_TIMEOUT_SEC)
        if response is None:
            return {'ok': False, 'result': {}, 'error': 'saving the map timed out'}
        if not response.success:
            return {'ok': False, 'result': {}, 'error': response.error_msg}
        return {
            'ok': True, 'error': '',
            'result': json.loads(response.result_json or '{}'),
        }

    def drive(self, keys: list[str], boost: bool = False) -> dict:
        """Applies a manual driving command from the browser.

        The browser sends which keys are held, never a velocity: the speeds come
        from this node's parameters, so what a client can ask for is bounded by
        the robot's configuration. The command is a refresh of the deadman, not
        a latch — stop sending and the robot stops on its own (see teleop.py).

        Args:
            keys: Keys currently held, e.g. ["w", "a"].
            boost: True while shift is held.

        Returns:
            Dict with the resulting "linear", "angular" and "driving" state.
        """
        # Parameters are read per command so `ros2 param set` retunes the
        # speeds live, mid-session, without restarting the dashboard.
        linear, angular = twist_from_keys(
            keys,
            self.get_parameter('teleop_linear_speed').value,
            self.get_parameter('teleop_angular_speed').value,
            boost=boost,
        )
        self._teleop.update(linear, angular, time.monotonic())
        driving = linear != 0.0 or angular != 0.0
        if driving and not self._teleop_driving:
            self.get_logger().info('Manual driving engaged from the dashboard (WASD)')
        self._teleop_driving = driving
        return {'linear': linear, 'angular': angular, 'driving': driving}

    def stop_driving(self) -> dict:
        """Ends the manual driving session; the robot is sent an explicit stop."""
        self._teleop.stop()
        if self._teleop_driving:
            self.get_logger().info('Manual driving released')
        self._teleop_driving = False
        return {'linear': 0.0, 'angular': 0.0, 'driving': False}

    def _reindex_zones(self) -> None:
        # One-shot: waits (every 3 s, without blocking anything) for
        # /rag/update_map, indexes every stored zone once, then stops.
        if self._zones_reindexed or not self._update_map_client.service_is_ready():
            return
        zones = self.zones.load_all()
        for name, area in zones.items():
            self.index_zone_in_memory(name, area)
        self._zones_reindexed = True
        self._reindex_timer.cancel()
        if zones:
            self.get_logger().info(
                f'Re-indexed {len(zones)} zone(s) into the active memory session',
            )

    def _publish_teleop(self) -> None:
        # Timer at teleop_rate_hz. While nobody drives, TeleopState.tick returns
        # None and NOTHING is published: /cmd_vel stays entirely Nav2's, and an
        # idle dashboard cannot interfere with an autonomous goal.
        command = self._teleop.tick(time.monotonic())
        if command is None:
            return
        linear, angular = command
        self._cmd_vel_pub.publish(self._build_twist(linear, angular))
        if self._teleop_driving and linear == 0.0 and angular == 0.0:
            self._teleop_driving = False
            self.get_logger().warning(
                'Manual driving command went stale (browser closed or unresponsive) '
                '— robot stopped',
            )

    def _build_twist(self, linear: float, angular: float):
        """Builds the velocity message in the flavour this stack listens for."""
        if not self._teleop_stamped:
            message = Twist()
            message.linear.x = linear
            message.angular.z = angular
            return message
        message = TwistStamped()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = 'base_link'
        message.twist.linear.x = linear
        message.twist.angular.z = angular
        return message

    def _call_service(self, client, request, timeout_sec: float):
        """Calls a service from an HTTP thread and waits for the response.

        Event-based wait, never a nested spin: the clients live in their own
        callback group, so an executor thread delivers the response while this
        uvicorn thread blocks (ADR-007). A timeout cancels the request and
        returns None rather than leaving the browser hanging.

        Args:
            client: The service client to call.
            request: The request message.
            timeout_sec: How long to wait for the response.

        Returns:
            The response message, or None if it did not arrive in time.
        """
        future = client.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _: done.set())
        if not done.wait(timeout=timeout_sec):
            future.cancel()
            return None
        return future.result()

    def _on_goal(self, msg: String) -> None:
        self.events.append('goal', msg.data)

    def _on_status(self, msg: String) -> None:
        self.events.append('status', msg.data)

    def _on_response(self, msg: String) -> None:
        self.events.append('response', msg.data)

    def _on_rosout(self, msg: Log) -> None:
        if msg.level < 20 or msg.name == 'dashboard_node':
            return
        self.events.append('rosout', msg.msg, source=msg.name, level=msg.level)

    def _on_map(self, msg: OccupancyGrid) -> None:
        with self._map_lock:
            self._latest_map = msg

    def _run_http_server(self, host: str, port: int) -> None:
        app = build_app(self)
        config = uvicorn.Config(app, host=host, port=port, log_level='warning')
        uvicorn.Server(config).run()


def main(args: list[str] | None = None) -> None:
    """Entry point for the dashboard_node executable."""
    rclpy.init(args=args)
    node = DashboardNode()
    # MultiThreadedExecutor so the map-update client's response (own callback
    # group) is delivered while the topic subscriptions run, and so the node's
    # callbacks are not starved by uvicorn's server thread calling into it.
    executor = rclpy.executors.MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        # ExternalShutdownException is how rclpy reports SIGINT/SIGTERM from
        # `ros2 launch` shutting the stack down — an ordinary stop, not a crash.
        pass
    finally:
        # Stop the executor's worker threads BEFORE destroying the node. uvicorn
        # runs in a daemon thread that keeps calling into the node, and with
        # multiple executor threads still live a concurrent destroy_node races
        # the rmw teardown into a segfault. Shutting the executor first joins
        # the workers so teardown is single-threaded and safe.
        executor.shutdown()
        node.destroy_node()
        # Guarded: on external shutdown the context is already down and an
        # unconditional shutdown() raises RCLError over the real exit.
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
