"""ROS 2 node serving a web dashboard for observability, goals, and zone editing."""

import os
import threading
import time
from pathlib import Path

import uvicorn

import rclpy
from nav_msgs.msg import OccupancyGrid
from rcl_interfaces.msg import Log
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
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
from robot_interfaces.srv import UpdateMap
from robot_zones.zone_store import ZoneStore

from robot_dashboard.web_api import build_app, render_map_png

# Workspace root for the default data paths. Reads ROBOT_WS (exported by
# setup_env.sh) so the package is not tied to one developer's home directory;
# the path is still overridable as a ROS 2 parameter.
WS_ROOT = Path(os.environ.get('ROBOT_WS', Path.home() / 'robot_ws'))

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
    interactive SLAM map (robot pose, named zones, area selection), and a
    goal input box with browser-based voice recognition.

    Subscribes:
        /robot/goal (std_msgs/String): Goals sent by any client, echoed to the timeline.
        /robot/status (std_msgs/String): Planner execution status.
        /robot/response (std_msgs/String): Final responses to the user.
        /rosout (rcl_interfaces/Log): Aggregated logs from every node.
        /map (nav_msgs/OccupancyGrid): SLAM map, rendered in the UI.

    Publishes:
        /robot/goal (std_msgs/String): Goals submitted through the web UI.

    Services (client):
        /rag/update_map (UpdateMap): Indexes named zones into semantic memory.

    Parameters:
        http_host (str): Bind address for the HTTP server. Default: 127.0.0.1
            (loopback only — the API has no authentication; see
            config/agent_params.yaml to expose it on the network).
        http_port (int): Port for the HTTP server. Default: 8080
        zones_db (str): SQLite file where named zones are persisted.
    """

    def __init__(self) -> None:
        super().__init__('dashboard_node')
        self.declare_parameter('http_host', '127.0.0.1')
        self.declare_parameter('http_port', 8080)
        self.declare_parameter('zones_db', str(WS_ROOT / 'data' / 'zones.db'))

        self.events = EventBuffer()
        self.zones = ZoneStore(self.get_parameter('zones_db').value)
        self._map_lock = threading.Lock()
        self._latest_map: OccupancyGrid | None = None
        self._map_png_b64: str | None = None
        self._map_png_stamp: int = -1

        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        self._goal_pub = self.create_publisher(String, '/robot/goal', 10)
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

        The PNG is re-rendered only when a newer grid has arrived. Rows are
        flipped so the image's top edge corresponds to y_max.
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
        obj.label = name
        obj.confidence = 1.0
        obj.pose.position.x = (area['x_min'] + area['x_max']) / 2.0
        obj.pose.position.y = (area['y_min'] + area['y_max']) / 2.0
        obj.description = (
            f'User-defined zone "{name}" covering x[{area["x_min"]:.2f}, {area["x_max"]:.2f}] '
            f'y[{area["y_min"]:.2f}, {area["y_max"]:.2f}] in the map frame. '
            f'The robot can navigate to it or explore inside it by name.'
        )
        obj.room_zone = name
        obj.timestamp = self.get_clock().now().to_msg()
        self._update_map_client.call_async(UpdateMap.Request(object_data=obj))

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
