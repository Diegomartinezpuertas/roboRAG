"""ROS 2 node serving a web dashboard for observability, goals, and zone editing."""

import base64
import io
import threading
import time
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image as PILImage
from pydantic import BaseModel

import rclpy
from nav_msgs.msg import OccupancyGrid
from rcl_interfaces.msg import Log
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

from robot_dashboard.web_page import PAGE

MAP_QOS = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)

# Occupancy value -> RGB: unknown, free, occupied.
COLOR_UNKNOWN = (43, 48, 62)
COLOR_FREE = (222, 227, 236)
# Strong red + 1-cell dilation: at 0.05 m/px walls are 1px thin and near-black
# was invisible once scaled in the canvas.
COLOR_OCCUPIED = (226, 76, 61)


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
        http_host (str): Bind address for the HTTP server. Default: 0.0.0.0
        http_port (int): Port for the HTTP server. Default: 8080
        zones_db (str): SQLite file where named zones are persisted.
    """

    def __init__(self) -> None:
        super().__init__('dashboard_node')
        self.declare_parameter('http_host', '0.0.0.0')
        self.declare_parameter('http_port', 8080)
        self.declare_parameter('zones_db', '/home/diego/robot_ws/data/zones.db')

        self.events = EventBuffer()
        self.zones = ZoneStore(self.get_parameter('zones_db').value)
        self._map_lock = threading.Lock()
        self._latest_map: OccupancyGrid | None = None
        self._map_png_b64: str | None = None
        self._map_png_stamp: int = -1

        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        self._goal_pub = self.create_publisher(String, '/robot/goal', 10)
        self._update_map_client = self.create_client(UpdateMap, '/rag/update_map')
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
                self._map_png_b64 = _render_map_png(grid)
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
        """Upserts a named zone into semantic memory via /rag/update_map.

        Args:
            name: Zone name, e.g. "cocina".
            area: {x_min, y_min, x_max, y_max} in map-frame meters.
        """
        if not self._update_map_client.wait_for_service(timeout_sec=2.0):
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


def _render_map_png(grid: OccupancyGrid) -> str:
    """Renders an OccupancyGrid to a base64 PNG (top row = y_max).

    Occupied cells are dilated by one cell so walls stay visible when the
    canvas scales the image.
    """
    width, height = grid.info.width, grid.info.height
    image = PILImage.new('RGB', (width, height))
    pixels = image.load()
    data = grid.data

    occupied: set[tuple[int, int]] = set()
    for row in range(height):
        image_row = height - 1 - row
        base = row * width
        for col in range(width):
            value = data[base + col]
            if value == -1:
                pixels[col, image_row] = COLOR_UNKNOWN
            elif value < 50:
                pixels[col, image_row] = COLOR_FREE
            else:
                occupied.add((col, image_row))

    for col, image_row in occupied:
        for d_col in (-1, 0, 1):
            for d_row in (-1, 0, 1):
                n_col, n_row = col + d_col, image_row + d_row
                if 0 <= n_col < width and 0 <= n_row < height:
                    pixels[n_col, n_row] = COLOR_OCCUPIED

    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


class GoalRequest(BaseModel):
    """Body schema for POST /api/goal."""

    text: str


class ZoneRequest(BaseModel):
    """Body schema for POST /api/zones."""

    name: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float


def build_app(node: DashboardNode) -> FastAPI:
    """Builds the FastAPI application bound to a dashboard node.

    Args:
        node: The dashboard node providing events, map, zones, and goal publishing.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(title='Robot RAG Agent Dashboard')

    @app.get('/', response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get('/api/events')
    def events(since: int = 0) -> dict:
        return {'events': node.events.since(since)}

    @app.get('/api/map')
    def map_snapshot() -> dict:
        return {
            'map': node.get_map_snapshot(),
            'robot': node.get_robot_pose(),
            'zones': node.zones.load_all(),
        }

    @app.post('/api/goal')
    def send_goal(body: GoalRequest) -> JSONResponse:
        text = body.text.strip()
        if not text:
            return JSONResponse({'ok': False, 'error': 'empty goal'}, status_code=400)
        node.publish_goal(text)
        node.get_logger().info(f'Goal submitted via dashboard: {text}')
        return JSONResponse({'ok': True})

    @app.post('/api/zones')
    def save_zone(body: ZoneRequest) -> JSONResponse:
        name = body.name.strip().lower().replace(' ', '_')
        if not name:
            return JSONResponse({'ok': False, 'error': 'empty name'}, status_code=400)
        area = {
            'x_min': min(body.x_min, body.x_max),
            'y_min': min(body.y_min, body.y_max),
            'x_max': max(body.x_min, body.x_max),
            'y_max': max(body.y_min, body.y_max),
        }
        node.zones.save(name, area)
        node.index_zone_in_memory(name, area)
        node.get_logger().info(f'Zone saved via dashboard: {name} {area}')
        return JSONResponse({'ok': True, 'name': name})

    @app.delete('/api/zones/{name}')
    def delete_zone(name: str) -> JSONResponse:
        if not node.zones.delete(name):
            return JSONResponse({'ok': False, 'error': 'unknown zone'}, status_code=404)
        return JSONResponse({'ok': True})

    return app


def main(args: list[str] | None = None) -> None:
    """Entry point for the dashboard_node executable."""
    rclpy.init(args=args)
    node = DashboardNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
