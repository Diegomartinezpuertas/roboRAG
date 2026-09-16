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

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from PIL import Image as PILImage
from pydantic import BaseModel, Field

from robot_zones.room_semantics import classify_room, suggested_zone_names

from robot_dashboard.web_page import PAGE

# Upper bound on how many memory entries one /api/memory call may return.
# The viewer is a browser panel, not an export tool.
MAX_MEMORY_ENTRIES = 200

# Smallest side, in meters, of a zone box dropped at the robot's position. A
# TurtleBot3 Waffle is 0.28 m across; anything smaller is not a place.
MIN_ZONE_SIZE = 0.4

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


def box_around(x: float, y: float, size: float) -> dict:
    """Builds a square zone centered on a point, for "name the room I am in".

    Driving into a room and naming it is the fast way to label a house; the
    exact rectangle matters less than being inside it, so the UI only asks for
    a side length.

    Args:
        x: Center x in map-frame meters (the robot's position).
        y: Center y in map-frame meters.
        size: Side length in meters, clamped to at least MIN_ZONE_SIZE.

    Returns:
        Dict with x_min/y_min/x_max/y_max keys.
    """
    half = max(MIN_ZONE_SIZE, size) / 2.0
    return {'x_min': x - half, 'y_min': y - half, 'x_max': x + half, 'y_max': y + half}


def memory_entry_title(metadata: dict, entry_id: str) -> str:
    """Names one memory entry for the viewer's card header.

    Each collection labels itself differently — a semantic object carries a
    label and a zone, a knowledge chunk carries its source file, a task carries
    its id — so the title is whatever that entry actually knows about itself,
    falling back to the raw id.

    Args:
        metadata: The entry's ChromaDB metadata.
        entry_id: The entry's id, used when metadata says nothing useful.

    Returns:
        A short title string.
    """
    label = str(metadata.get('label') or '')
    zone = str(metadata.get('room_zone') or '')
    if label and zone:
        return f'{label} · {zone}'
    return label or zone or str(
        metadata.get('source') or metadata.get('task_id') or entry_id,
    )


def format_memory_entry(item: dict, active_map_id: str) -> dict:
    """Turns a raw /rag/inspect entry into the flat record the viewer renders.

    Everything the UI needs is pulled out of the metadata here — coordinates,
    zone, provenance, and whether the entry belongs to a map session that is no
    longer live — so the page renders fields instead of parsing prose.

    Args:
        item: One entry from /rag/inspect: {id, document, metadata, score}.
        active_map_id: The map session currently in use (ADR-019).

    Returns:
        Dict with id, title, document, score, label, zone, source, x, y,
        map_id, observations and stale. `x`/`y` are None for entries without a pose, and
        `stale` marks a coordinate memory from a different map.
    """
    metadata = item.get('metadata') or {}
    entry_id = str(item.get('id', ''))
    entry_map_id = str(metadata.get('map_id') or '')
    return {
        'id': entry_id,
        'title': memory_entry_title(metadata, entry_id),
        'document': item.get('document') or '',
        'score': item.get('score', -1.0),
        'label': str(metadata.get('label') or ''),
        'zone': str(metadata.get('room_zone') or ''),
        'source': str(metadata.get('source') or metadata.get('task_id') or ''),
        'x': _as_float(metadata.get('pose_x')),
        'y': _as_float(metadata.get('pose_y')),
        'map_id': entry_map_id,
        # How many times the robot observed this place (merge-on-write folds
        # re-observations into one memory, ADR-025); 1 for everything else.
        'observations': _as_count(metadata.get('observations')),
        # An entry written against another map points somewhere else entirely
        # today; the viewer greys it out instead of hiding it, because seeing
        # that it exists is half of understanding the memory.
        'stale': bool(entry_map_id and active_map_id and entry_map_id != active_map_id),
    }


def group_memory_entries(entries: list[dict]) -> list[dict]:
    """Folds formatted entries that are the same thing into one card each.

    Merge-on-write keeps one memory per place and look (ADR-025), but two kinds
    of repetition are legitimate in storage and still noise on screen: several
    facts about one exact spot (a benchmark landmark and its seeded description
    share coordinates on purpose), and identical documents (the same benchmark
    task logged run after run). Storage keeps them apart — they are separate
    facts and separate events — and the viewer shows each once.

    Args:
        entries: Records from format_memory_entry, in display order.

    Returns:
        One record per group, in order of first appearance. Each is its first
        member's record plus "count" (members), "ids" (all member ids),
        "facts" ({title, document} per member), the best "score" of the group,
        the members' titles joined as "title", and "stale" only if every member
        is stale.
    """
    groups: dict[tuple, dict] = {}
    for entry in entries:
        if entry['x'] is not None and entry['y'] is not None:
            key = ('pose', entry['map_id'], round(entry['x'], 2), round(entry['y'], 2))
        else:
            key = ('doc', entry['document'])
        group = groups.get(key)
        if group is None:
            groups[key] = {
                **entry, 'count': 1, 'ids': [entry['id']],
                'facts': [{'title': entry['title'], 'document': entry['document']}],
            }
            continue
        group['count'] += 1
        group['ids'].append(entry['id'])
        group['facts'].append({'title': entry['title'], 'document': entry['document']})
        group['score'] = max(group['score'], entry['score'])
        group['stale'] = group['stale'] and entry['stale']
        group['observations'] = max(group['observations'], entry['observations'])
        if entry['title'] not in group['title'].split(' / '):
            group['title'] = f"{group['title']} / {entry['title']}"
    return list(groups.values())


def _as_count(value) -> int:
    """Returns a positive observation count, 1 when missing or malformed."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 1


def _as_float(value) -> float | None:
    """Returns value as a float, or None when it is missing or not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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


class ZoneHereRequest(BaseModel):
    """Body schema for POST /api/zones/here."""

    name: str
    size: float = 2.0


class TeleopRequest(BaseModel):
    """Body schema for POST /api/teleop: the keys the browser currently holds."""

    keys: list[str] = Field(default_factory=list)
    boost: bool = False


class SaveMapRequest(BaseModel):
    """Body schema for POST /api/map/save."""

    name: str = ''


def build_app(node) -> FastAPI:
    """Builds the FastAPI application bound to a dashboard node.

    Args:
        node: Any object providing `events.since(int)`, `get_map_snapshot()`,
            `get_robot_pose()`, `zones` (a ZoneStore), `publish_goal(str)`,
            `index_zone_in_memory(str, dict)`, `inspect_memory(str, str, int,
            bool)`, `save_map(str)`, `drive(list[str], bool)`, `stop_driving()`
            and `get_logger()`. In production this is a `DashboardNode`; in
            tests, a stub.

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
        # The PNG itself is served by /api/map/png with an ETag, so this stays
        # light enough to poll for the robot pose every couple of seconds.
        snapshot = node.get_map_snapshot()
        if snapshot is not None:
            snapshot = {k: v for k, v in snapshot.items() if k != 'png_b64'}
        return {
            'map': snapshot,
            'robot': node.get_robot_pose(),
            'zones': node.zones.load_all(),
        }

    @app.get('/api/map/png')
    def map_png(request: Request) -> Response:
        # The rendered map only changes when SLAM publishes a newer grid, while
        # the UI polls every 2 s — so the PNG (tens of KB of base64) used to be
        # resent unchanged most of the time. The grid's stamp is the version:
        # sent as an ETag, answered with 304 when the client already has it.
        snapshot = node.get_map_snapshot()
        if snapshot is None:
            return Response(status_code=404)
        etag = f'"{snapshot["stamp"]}"'
        headers = {'ETag': etag, 'Cache-Control': 'no-cache'}
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        return Response(
            content=base64.b64decode(snapshot['png_b64']),
            media_type='image/png', headers=headers,
        )

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
        return JSONResponse({'ok': True, 'name': name, 'room_type': _room_type(name)})

    @app.delete('/api/zones/{name}')
    def delete_zone(name: str) -> JSONResponse:
        if not node.zones.delete(name):
            return JSONResponse({'ok': False, 'error': 'unknown zone'}, status_code=404)
        return JSONResponse({'ok': True})

    @app.post('/api/zones/here')
    def save_zone_here(body: ZoneHereRequest) -> JSONResponse:
        # Naming the room you are standing in, for the manual mapping run:
        # drive in, type "cocina", carry on. Same storage and same indexing as
        # a dragged rectangle — only the geometry comes from the robot's pose.
        name = normalize_zone_name(body.name)
        if not name:
            return JSONResponse({'ok': False, 'error': 'empty name'}, status_code=400)
        pose = node.get_robot_pose()
        if pose is None:
            return JSONResponse(
                {'ok': False, 'error': 'robot pose unknown (no map->base_link transform yet)'},
                status_code=409,
            )
        area = box_around(pose['x'], pose['y'], body.size)
        node.zones.save(name, area)
        node.index_zone_in_memory(name, area)
        node.get_logger().info(f'Zone saved at the robot pose: {name} {area}')
        return JSONResponse(
            {'ok': True, 'name': name, 'area': area, 'room_type': _room_type(name)},
        )

    @app.get('/api/room-types')
    def room_types() -> dict:
        # Autocomplete for the zone name box. Naming a zone "cocina" rather
        # than "zona_1" is what makes it findable by function (ADR-022), so the
        # UI suggests exactly the names the classifier understands.
        return {'names': suggested_zone_names()}

    @app.get('/api/memory')
    def memory(
        collection: str = 'semantic_map', q: str = '',
        limit: int = 50, active_only: bool = True,
    ) -> dict:
        # Read-only window on the robot's RAG memory (ADR-024). Always 200:
        # rag_node may legitimately not be up yet (the dashboard starts first),
        # and the UI shows that as a message in the panel rather than as a
        # failed request.
        result = node.inspect_memory(
            collection, q.strip(), max(1, min(limit, MAX_MEMORY_ENTRIES)), active_only,
        )
        return {
            'ok': result['ok'],
            'collection': collection,
            'query': q.strip(),
            'map_id': result['map_id'],
            'stats': result['stats'],
            # Grouped for display only: the same spot or the same document once,
            # with its members listed (storage keeps them apart, ADR-025).
            'entries': group_memory_entries([
                format_memory_entry(item, result['map_id']) for item in result['items']
            ]),
            'error': result['error'],
        }

    @app.post('/api/teleop')
    def teleop(body: TeleopRequest) -> dict:
        # One request per key press, release, and refresh while held. The node
        # turns the key set into a velocity and restarts its deadman; stop
        # calling and the robot stops by itself (ADR-023).
        return {'ok': True, **node.drive(body.keys, body.boost)}

    @app.post('/api/teleop/stop')
    def teleop_stop() -> dict:
        return {'ok': True, **node.stop_driving()}

    @app.post('/api/map/save')
    def save_map(body: SaveMapRequest) -> JSONResponse:
        # The name doubles as a directory name and as the map-session id the
        # memories are tagged with, so it goes through the same normalization
        # as a zone name.
        result = node.save_map(normalize_zone_name(body.name))
        if not result['ok']:
            return JSONResponse(
                {'ok': False, 'error': result['error']}, status_code=503,
            )
        node.get_logger().info(f'SLAM map saved via dashboard: {result["result"]}')
        return JSONResponse({'ok': True, 'result': result['result']})

    return app


def _room_type(zone_name: str) -> str:
    """Returns the room type a zone name denotes, or '' when it denotes none."""
    room = classify_room(zone_name)
    return room.key if room is not None else ''

