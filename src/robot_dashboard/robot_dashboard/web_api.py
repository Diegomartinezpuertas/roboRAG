"""HTTP layer of the dashboard: FastAPI app, request models, and map rendering.

Deliberately free of any `rclpy` import. The app is built against a *node
interface* (the handful of methods listed in `build_app`), not against
`DashboardNode` itself, so the whole HTTP surface can be exercised with a stub
in the pure-logic test suite — which runs without a ROS installation. The ROS
node lives in `dashboard_node.py` and supplies the real implementation.

See docs/decisions/ADR-018-test-strategy.md.
"""

import base64
import io

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image as PILImage
from pydantic import BaseModel

from robot_dashboard.web_page import PAGE

# Occupancy value -> RGB: unknown, free, occupied.
COLOR_UNKNOWN = (43, 48, 62)
COLOR_FREE = (222, 227, 236)
# Strong red + 1-cell dilation: at 0.05 m/px walls are 1px thin and near-black
# was invisible once scaled in the canvas.
COLOR_OCCUPIED = (226, 76, 61)


def render_map_png(grid) -> str:
    """Renders an OccupancyGrid to a base64 PNG (top row = y_max).

    Occupied cells are dilated by one cell so walls stay visible when the
    canvas scales the image.

    Args:
        grid: Any object exposing nav_msgs/OccupancyGrid's `info` (width,
            height) and `data` fields. Duck-typed so the test suite can pass a
            plain stub without a ROS message type.

    Returns:
        Base64-encoded PNG bytes, ASCII string.
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


def normalize_zone_name(raw: str) -> str:
    """Normalizes a user-supplied zone name to its storage key.

    Lowercased with spaces collapsed to underscores, so "Sala de Estar" and
    "sala de estar" address the same zone.

    Args:
        raw: Name as typed in the dashboard.

    Returns:
        The normalized key, or '' if the input was blank.
    """
    return '_'.join(raw.strip().lower().split())


def normalize_area(x_min: float, y_min: float, x_max: float, y_max: float) -> dict:
    """Orders a dragged rectangle's corners into a min/max area.

    The UI lets the user drag in any direction, so the raw corners arrive
    unordered.

    Args:
        x_min, y_min, x_max, y_max: Raw rectangle corners in map-frame meters.

    Returns:
        Dict with correctly ordered x_min/y_min/x_max/y_max keys.
    """
    return {
        'x_min': min(x_min, x_max),
        'y_min': min(y_min, y_max),
        'x_max': max(x_min, x_max),
        'y_max': max(y_min, y_max),
    }


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


def build_app(node) -> FastAPI:
    """Builds the FastAPI application bound to a dashboard node.

    Args:
        node: Any object providing `events.since(int)`, `get_map_snapshot()`,
            `get_robot_pose()`, `zones` (a ZoneStore), `publish_goal(str)`,
            `index_zone_in_memory(str, dict)` and `get_logger()`. In production
            this is a `DashboardNode`; in tests, a stub.

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
        name = normalize_zone_name(body.name)
        if not name:
            return JSONResponse({'ok': False, 'error': 'empty name'}, status_code=400)
        area = normalize_area(body.x_min, body.y_min, body.x_max, body.y_max)
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
