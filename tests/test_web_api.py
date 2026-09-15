"""Tests for the dashboard HTTP layer (robot_dashboard.web_api).

The app is built against a node interface rather than DashboardNode itself, so
the whole HTTP surface runs here with a stub and no ROS installation. This is
the layer a user actually touches — every endpoint, both its success and its
failure path, is covered. See docs/decisions/ADR-018-test-strategy.md.
"""

import pytest
from fastapi.testclient import TestClient

from robot_dashboard.web_api import (
    build_app,
    normalize_area,
    normalize_zone_name,
    render_map_png,
)


class FakeLogger:
    """Collects log lines instead of writing to /rosout."""

    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)

    def warning(self, message):
        self.messages.append(message)


class FakeEvents:
    def __init__(self, events=None):
        self._events = events or []

    def since(self, last_id):
        return [e for e in self._events if e['id'] > last_id]


class FakeZones:
    """Minimal in-memory stand-in for ZoneStore."""

    def __init__(self, zones=None):
        self.zones = dict(zones or {})

    def load_all(self):
        return dict(self.zones)

    def save(self, name, area):
        self.zones[name] = area

    def delete(self, name):
        return self.zones.pop(name, None) is not None


class FakeNode:
    """Stub implementing the node interface build_app depends on."""

    def __init__(self, zones=None, events=None, map_snapshot=None, pose=None):
        self.events = FakeEvents(events)
        self.zones = FakeZones(zones)
        self.published_goals = []
        self.indexed_zones = []
        self._map_snapshot = map_snapshot
        self._pose = pose
        self._logger = FakeLogger()

    def get_logger(self):
        return self._logger

    def publish_goal(self, text):
        self.published_goals.append(text)

    def index_zone_in_memory(self, name, area):
        self.indexed_zones.append((name, area))

    def get_map_snapshot(self):
        return self._map_snapshot

    def get_robot_pose(self):
        return self._pose


@pytest.fixture
def node():
    return FakeNode()


@pytest.fixture
def client(node):
    return TestClient(build_app(node))


# --- index -----------------------------------------------------------------

def test_index_serves_the_single_page_app(client):
    response = client.get('/')
    assert response.status_code == 200
    assert '<canvas id="mapCanvas">' in response.text


# --- /api/events -----------------------------------------------------------

def test_events_returns_everything_when_since_is_zero():
    events = [{'id': 1, 'text': 'a'}, {'id': 2, 'text': 'b'}]
    client = TestClient(build_app(FakeNode(events=events)))
    assert client.get('/api/events').json() == {'events': events}


def test_events_returns_only_newer_than_since():
    events = [{'id': 1, 'text': 'a'}, {'id': 2, 'text': 'b'}, {'id': 3, 'text': 'c'}]
    client = TestClient(build_app(FakeNode(events=events)))
    body = client.get('/api/events', params={'since': 2}).json()
    assert [e['id'] for e in body['events']] == [3]


# --- /api/goal -------------------------------------------------------------

def test_goal_is_published_to_the_node(client, node):
    response = client.post('/api/goal', json={'text': 've a la cocina'})
    assert response.status_code == 200
    assert response.json() == {'ok': True}
    assert node.published_goals == ['ve a la cocina']


def test_goal_is_stripped_before_publishing(client, node):
    client.post('/api/goal', json={'text': '  explora la sala  '})
    assert node.published_goals == ['explora la sala']


@pytest.mark.parametrize('text', ['', '   ', '\n\t'])
def test_blank_goal_is_rejected_and_never_published(client, node, text):
    response = client.post('/api/goal', json={'text': text})
    assert response.status_code == 400
    assert response.json()['error'] == 'empty goal'
    assert node.published_goals == []


def test_goal_without_a_text_field_is_a_validation_error(client):
    assert client.post('/api/goal', json={}).status_code == 422


# --- /api/zones ------------------------------------------------------------

def test_saving_a_zone_stores_it_and_indexes_it_in_memory(client, node):
    response = client.post('/api/zones', json={
        'name': 'cocina', 'x_min': 0.0, 'y_min': 0.0, 'x_max': 1.0, 'y_max': 2.0,
    })
    assert response.status_code == 200
    assert response.json() == {'ok': True, 'name': 'cocina'}
    assert node.zones.load_all()['cocina'] == {
        'x_min': 0.0, 'y_min': 0.0, 'x_max': 1.0, 'y_max': 2.0,
    }
    assert node.indexed_zones[0][0] == 'cocina'


def test_zone_name_is_normalized(client, node):
    response = client.post('/api/zones', json={
        'name': '  Sala De Estar  ', 'x_min': 0, 'y_min': 0, 'x_max': 1, 'y_max': 1,
    })
    assert response.json()['name'] == 'sala_de_estar'
    assert 'sala_de_estar' in node.zones.load_all()


def test_a_rectangle_dragged_backwards_is_normalized(client, node):
    """The UI lets the user drag in any direction, so corners arrive unordered."""
    client.post('/api/zones', json={
        'name': 'z', 'x_min': 3.0, 'y_min': 4.0, 'x_max': -1.0, 'y_max': -2.0,
    })
    assert node.zones.load_all()['z'] == {
        'x_min': -1.0, 'y_min': -2.0, 'x_max': 3.0, 'y_max': 4.0,
    }


@pytest.mark.parametrize('name', ['', '   '])
def test_blank_zone_name_is_rejected_and_nothing_is_stored(client, node, name):
    response = client.post('/api/zones', json={
        'name': name, 'x_min': 0, 'y_min': 0, 'x_max': 1, 'y_max': 1,
    })
    assert response.status_code == 400
    assert node.zones.load_all() == {}
    assert node.indexed_zones == []


def test_deleting_a_known_zone_succeeds():
    node = FakeNode(zones={'cocina': {'x_min': 0, 'y_min': 0, 'x_max': 1, 'y_max': 1}})
    client = TestClient(build_app(node))
    assert client.delete('/api/zones/cocina').status_code == 200
    assert node.zones.load_all() == {}


def test_deleting_an_unknown_zone_is_a_404(client):
    response = client.delete('/api/zones/no_existe')
    assert response.status_code == 404
    assert response.json()['error'] == 'unknown zone'


# --- /api/map --------------------------------------------------------------

def test_map_endpoint_reports_nulls_before_slam_publishes(client):
    """Every field is optional: the UI must cope with a stack that is still coming up."""
    assert client.get('/api/map').json() == {'map': None, 'robot': None, 'zones': {}}


def test_map_endpoint_returns_geometry_pose_and_zones_but_not_the_png():
    snapshot = {'png_b64': 'AAAA', 'resolution': 0.05, 'origin_x': -1.0,
                'origin_y': -2.0, 'width': 4, 'height': 4, 'stamp': 3}
    node = FakeNode(
        zones={'base': {'x_min': 0, 'y_min': 0, 'x_max': 1, 'y_max': 1}},
        map_snapshot=snapshot,
        pose={'x': 0.5, 'y': 1.5},
    )
    body = TestClient(build_app(node)).get('/api/map').json()
    # The image goes through /api/map/png (with an ETag); this poll stays light.
    assert body['map'] == {k: v for k, v in snapshot.items() if k != 'png_b64'}
    assert body['robot'] == {'x': 0.5, 'y': 1.5}
    assert 'base' in body['zones']


def test_map_png_endpoint_returns_an_image_with_an_etag():
    snapshot = {'png_b64': 'AAAA', 'resolution': 0.05, 'origin_x': -1.0,
                'origin_y': -2.0, 'width': 4, 'height': 4, 'stamp': 1234}
    node = FakeNode(map_snapshot=snapshot)
    response = TestClient(build_app(node)).get('/api/map/png')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/png'
    assert response.content == b'\x00\x00\x00'   # 'AAAA' decoded
    assert response.headers['etag'] == '"1234"'
    assert response.headers['cache-control'] == 'no-cache'


def test_map_png_endpoint_answers_304_when_the_client_has_the_current_version():
    snapshot = {'png_b64': 'AAAA', 'resolution': 0.05, 'origin_x': 0.0,
                'origin_y': 0.0, 'width': 4, 'height': 4, 'stamp': 1234}
    client = TestClient(build_app(FakeNode(map_snapshot=snapshot)))
    etag = client.get('/api/map/png').headers['etag']
    response = client.get('/api/map/png', headers={'If-None-Match': etag})
    assert response.status_code == 304
    assert response.content == b''
    assert response.headers['etag'] == etag


def test_map_png_endpoint_sends_the_image_again_once_the_map_changes():
    snapshot = {'png_b64': 'AAAA', 'resolution': 0.05, 'origin_x': 0.0,
                'origin_y': 0.0, 'width': 4, 'height': 4, 'stamp': 1234}
    node = FakeNode(map_snapshot=snapshot)
    client = TestClient(build_app(node))
    old_etag = client.get('/api/map/png').headers['etag']
    node._map_snapshot = {**snapshot, 'stamp': 5678}   # SLAM published a newer grid
    response = client.get('/api/map/png', headers={'If-None-Match': old_etag})
    assert response.status_code == 200
    assert response.headers['etag'] == '"5678"'


def test_map_png_endpoint_is_404_before_slam_publishes(client):
    assert client.get('/api/map/png').status_code == 404


# --- helpers ---------------------------------------------------------------

@pytest.mark.parametrize(('raw', 'expected'), [
    ('cocina', 'cocina'),
    ('  Cocina  ', 'cocina'),
    ('Sala De Estar', 'sala_de_estar'),
    ('sala   de   estar', 'sala_de_estar'),
    ('   ', ''),
])
def test_normalize_zone_name(raw, expected):
    assert normalize_zone_name(raw) == expected


def test_normalize_area_orders_corners():
    assert normalize_area(3.0, 4.0, -1.0, -2.0) == {
        'x_min': -1.0, 'y_min': -2.0, 'x_max': 3.0, 'y_max': 4.0,
    }


# --- map rendering ---------------------------------------------------------

class FakeGrid:
    """Duck-typed OccupancyGrid: only .info.width/.height and .data are read."""

    class Info:
        def __init__(self, width, height):
            self.width = width
            self.height = height

    def __init__(self, width, height, data):
        self.info = self.Info(width, height)
        self.data = data


def _decode(png_b64):
    import base64
    import io

    from PIL import Image
    return Image.open(io.BytesIO(base64.b64decode(png_b64)))


def test_render_map_png_produces_an_image_of_grid_size():
    grid = FakeGrid(4, 3, [-1] * 12)
    image = _decode(render_map_png(grid))
    assert image.size == (4, 3)


def test_render_map_png_colors_unknown_free_and_occupied_distinctly():
    # Row-major, bottom row first: unknown, then free, then occupied at the top.
    # The free cells are kept two rows clear of the occupied one, which would
    # otherwise overwrite its neighbours through the 1-cell dilation.
    grid = FakeGrid(1, 5, [-1, 0, 0, 0, 100])
    image = _decode(render_map_png(grid)).convert('RGB')
    colors = {image.getpixel((0, y)) for y in range(5)}
    assert len(colors) == 3, 'unknown, free and occupied must be visually distinct'


def test_render_map_png_flips_rows_so_the_top_of_the_image_is_y_max():
    # Grid row 0 (y_min) is free, row 1 (y_max) is unknown.
    grid = FakeGrid(1, 2, [0, -1])
    image = _decode(render_map_png(grid)).convert('RGB')
    top, bottom = image.getpixel((0, 0)), image.getpixel((0, 1))
    assert top != bottom
    # The unknown cell (grid row 1 = y_max) must be the TOP pixel.
    assert top == (43, 48, 62)


def test_render_map_png_dilates_occupied_cells():
    """Walls are 1px at 0.05 m/px and vanish when scaled, so they are dilated."""
    grid = FakeGrid(3, 3, [0, 0, 0, 0, 100, 0, 0, 0, 0])
    image = _decode(render_map_png(grid)).convert('RGB')
    occupied = (226, 76, 61)
    # The single occupied centre cell paints all 9 pixels.
    assert all(image.getpixel((x, y)) == occupied for x in range(3) for y in range(3))
