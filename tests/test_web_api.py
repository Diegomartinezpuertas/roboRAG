"""Tests for the dashboard HTTP layer (robot_dashboard.web_api).

The app is built against a node interface rather than DashboardNode itself, so
the whole HTTP surface runs here with a stub and no ROS installation. This is
the layer a user actually touches — every endpoint, both its success and its
failure path, is covered. See docs/decisions/ADR-018-test-strategy.md.
"""

import pytest
from fastapi.testclient import TestClient

from robot_dashboard.teleop import twist_from_keys
from robot_dashboard.web_api import (
    COLOR_OCCUPIED,
    COLOR_UNKNOWN,
    MAX_MEMORY_ENTRIES,
    box_around,
    build_app,
    format_memory_entry,
    group_memory_entries,
    memory_entry_title,
    normalize_area,
    normalize_zone_name,
    render_map_png,
)

# Speeds the fake node hands to twist_from_keys, standing in for the real
# node's parameters.
FAKE_LINEAR = 0.18
FAKE_ANGULAR = 1.0


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

    def clear(self):
        dropped = len(self._events)
        self._events.clear()
        return dropped


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

    def __init__(self, zones=None, events=None, map_snapshot=None, pose=None, memory=None):
        self.events = FakeEvents(events)
        self.zones = FakeZones(zones)
        self.published_goals = []
        self.indexed_zones = []
        self._map_snapshot = map_snapshot
        self._pose = pose
        self._logger = FakeLogger()
        # Memory, driving and map-saving state, recorded for assertions.
        self.memory_calls = []
        self.memory_result = memory or {
            'ok': True, 'items': [], 'stats': {}, 'map_id': '', 'error': '',
        }
        self.drive_calls = []
        self.sim_status = {'rtf': None, 'driver': None}
        self.delete_calls = []
        self.delete_result = {'ok': True, 'deleted': 2, 'error': ''}
        self.stop_calls = 0
        self.saved_maps = []
        self.save_map_result = {
            'ok': True, 'result': {'map_id': 'casa', 'path': '/tmp/casa.posegraph'}, 'error': '',
        }

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

    def inspect_memory(self, collection, query, limit, active_map_only):
        self.memory_calls.append((collection, query, limit, active_map_only))
        return self.memory_result

    def save_map(self, name):
        self.saved_maps.append(name)
        return self.save_map_result

    def drive(self, keys, boost=False):
        # The real node turns keys into a velocity with exactly this function;
        # the stub keeps that part real so the endpoint is tested end to end.
        self.drive_calls.append((list(keys), boost))
        linear, angular = twist_from_keys(keys, FAKE_LINEAR, FAKE_ANGULAR, boost=boost)
        return {'linear': linear, 'angular': angular, 'driving': bool(linear or angular)}

    def stop_driving(self):
        self.stop_calls += 1
        return {'linear': 0.0, 'angular': 0.0, 'driving': False}

    def get_sim_status(self):
        return self.sim_status

    def delete_memory(self, collection, ids):
        self.delete_calls.append((collection, list(ids)))
        return self.delete_result


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
    # room_type is what the name was recognized as (ADR-022); the room-type
    # tests below cover the cases that matter.
    assert response.json() == {'ok': True, 'name': 'cocina', 'room_type': 'kitchen'}
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
    assert client.get('/api/map').json() == {
        'map': None, 'robot': None, 'zones': {}, 'sim': {'rtf': None, 'driver': None},
    }


def test_map_endpoint_carries_sim_speed_and_who_drives():
    """What the drive panel needs to explain a robot that does not seem to move."""
    node = FakeNode(pose={'x': 1.0, 'y': 2.0, 'yaw': 1.57})
    node.sim_status = {'rtf': 0.62, 'driver': 'teleop'}
    body = TestClient(build_app(node)).get('/api/map').json()
    assert body['sim'] == {'rtf': 0.62, 'driver': 'teleop'}
    assert body['robot']['yaw'] == 1.57


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
    version = body['map'].pop('version')
    assert body['map'] == {k: v for k, v in snapshot.items() if k != 'png_b64'}
    assert version.endswith('-3')
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
    assert response.headers['etag'].endswith('-1234"')
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
    assert response.headers['etag'].endswith('-5678"')


def test_a_relaunched_dashboard_never_serves_a_previous_runs_image_as_current():
    """Regression: the ETag was the grid stamp, which is simulation time and
    restarts at zero every launch. A browser holding a previous run's image
    with the same stamp got 304 and kept showing the old map — the "sometimes
    the map is red and grey" report, after the rendering changed colours."""
    snapshot = {'png_b64': 'AAAA', 'resolution': 0.05, 'origin_x': 0.0,
                'origin_y': 0.0, 'width': 4, 'height': 4, 'stamp': 1234}
    first_run = TestClient(build_app(FakeNode(map_snapshot=snapshot)))
    old_etag = first_run.get('/api/map/png').headers['etag']
    second_run = TestClient(build_app(FakeNode(map_snapshot=snapshot)))   # same stamp
    response = second_run.get('/api/map/png', headers={'If-None-Match': old_etag})
    assert response.status_code == 200
    assert second_run.get('/api/map').json()['map']['version'] != old_etag.strip('"')


def test_map_png_endpoint_is_404_before_slam_publishes(client):
    assert client.get('/api/map/png').status_code == 404


# --- /api/memory -----------------------------------------------------------

MEMORY_ITEMS = [
    {
        'id': 'zone-cocina',
        'document': 'kitchen at (x=1.25, y=-0.25) in cocina: User-defined zone "cocina" ...',
        'metadata': {
            'label': 'kitchen', 'room_zone': 'cocina',
            'pose_x': 1.25, 'pose_y': -0.25, 'map_id': 'abc123',
        },
        'score': 0.61,
    },
    {
        'id': 'environment_rules-0',
        'document': '## Safety rules\n\nThe robot must stop and report ...',
        'metadata': {'source': 'environment_rules.md', 'chunk_index': 0},
        'score': 0.42,
    },
    {
        'id': 'scene-2.0-1.0',
        'document': 'area at (x=2.00, y=1.00) in unknown area: predominantly white ...',
        'metadata': {'label': 'area', 'pose_x': 2.0, 'pose_y': 1.0, 'map_id': 'dead-map'},
        'score': 0.31,
    },
]


def _memory_node(**overrides):
    memory = {
        'ok': True, 'items': MEMORY_ITEMS, 'map_id': 'abc123',
        'stats': {'semantic_map': 12, 'knowledge_base': 30, 'task_history': 4}, 'error': '',
    }
    memory.update(overrides)
    return FakeNode(memory=memory)


def test_memory_flattens_entries_into_the_fields_the_viewer_renders():
    body = TestClient(build_app(_memory_node())).get('/api/memory').json()
    assert body['ok'] is True
    assert body['collection'] == 'semantic_map'
    assert body['map_id'] == 'abc123'
    assert body['stats']['knowledge_base'] == 30

    zone, knowledge, scene = body['entries']
    assert zone['title'] == 'kitchen · cocina'
    assert (zone['x'], zone['y']) == (1.25, -0.25)
    assert zone['score'] == 0.61
    assert zone['stale'] is False
    # A knowledge chunk has no pose at all; the UI must not try to map it.
    assert (knowledge['x'], knowledge['y']) == (None, None)
    assert knowledge['source'] == 'environment_rules.md'
    # And a memory written against another map is flagged, not hidden.
    assert scene['stale'] is True


def test_memory_defaults_to_the_active_map_and_a_bounded_page(client, node):
    client.get('/api/memory')
    assert node.memory_calls == [('semantic_map', '', 50, True)]


def test_memory_forwards_the_collection_query_and_scope(client, node):
    client.get('/api/memory', params={
        'collection': 'task_history', 'q': '  donde se cocina  ',
        'limit': 7, 'active_only': 'false',
    })
    assert node.memory_calls == [('task_history', 'donde se cocina', 7, False)]


@pytest.mark.parametrize(('requested', 'expected'), [(99999, MAX_MEMORY_ENTRIES), (0, 1), (-5, 1)])
def test_memory_clamps_the_requested_limit(client, node, requested, expected):
    """One browser panel must not be able to ask for the whole database."""
    client.get('/api/memory', params={'limit': requested})
    assert node.memory_calls[0][2] == expected


def test_memory_reports_a_down_service_without_failing_the_request():
    """rag_node starts after the dashboard; the panel says so instead of erroring."""
    node = _memory_node(ok=False, items=[], error='/rag/inspect unavailable')
    response = TestClient(build_app(node)).get('/api/memory')
    assert response.status_code == 200
    body = response.json()
    assert body['ok'] is False
    assert body['error'] == '/rag/inspect unavailable'
    assert body['entries'] == []


# --- /api/memory/delete (ADR-030) ------------------------------------------

def test_deleting_a_card_deletes_every_id_it_stands_for(client, node):
    response = client.post('/api/memory/delete', json={
        'collection': 'semantic_map', 'ids': ['landmark-estacion_a', 'scene-seed-estacion_a'],
    })
    assert response.status_code == 200
    assert response.json() == {'ok': True, 'deleted': 2}
    assert node.delete_calls == [
        ('semantic_map', ['landmark-estacion_a', 'scene-seed-estacion_a']),
    ]


def test_a_delete_without_ids_is_rejected_before_reaching_the_node(client, node):
    assert client.post('/api/memory/delete', json={'ids': ['', '  ']}).status_code == 400
    assert node.delete_calls == []


def test_a_refused_delete_reports_the_reason(client, node):
    node.delete_result = {
        'ok': False, 'deleted': 0,
        'error': 'task_history is rebuilt from files on disk; '
                 'only semantic_map memories can be deleted',
    }
    response = client.post('/api/memory/delete', json={'collection': 'task_history', 'ids': ['t']})
    assert response.status_code == 400
    assert 'only semantic_map' in response.json()['error']


# --- /api/teleop -----------------------------------------------------------

def test_teleop_turns_held_keys_into_a_velocity(client, node):
    body = client.post('/api/teleop', json={'keys': ['w', 'a']}).json()
    assert body['ok'] is True and body['driving'] is True
    assert body['linear'] == pytest.approx(FAKE_LINEAR)
    assert body['angular'] == pytest.approx(FAKE_ANGULAR)
    assert node.drive_calls == [(['w', 'a'], False)]


def test_teleop_without_keys_is_a_stop_and_not_an_error(client, node):
    """The browser sends the empty set on key release; that is a valid command."""
    body = client.post('/api/teleop', json={}).json()
    assert (body['linear'], body['angular'], body['driving']) == (0.0, 0.0, False)
    assert node.drive_calls == [([], False)]


def test_teleop_forwards_the_boost_flag(client, node):
    client.post('/api/teleop', json={'keys': ['w'], 'boost': True})
    assert node.drive_calls == [(['w'], True)]


def test_teleop_stop_releases_the_robot(client, node):
    body = client.post('/api/teleop/stop').json()
    assert body == {'ok': True, 'linear': 0.0, 'angular': 0.0, 'driving': False}
    assert node.stop_calls == 1


# --- /api/map/save ---------------------------------------------------------

def test_saving_the_map_normalizes_the_name_and_returns_the_skill_result(client, node):
    response = client.post('/api/map/save', json={'name': '  Casa De Diego '})
    assert response.status_code == 200
    assert response.json() == {
        'ok': True, 'result': {'map_id': 'casa', 'path': '/tmp/casa.posegraph'},
    }
    # The name doubles as a directory and as a map-session id (ADR-019).
    assert node.saved_maps == ['casa_de_diego']


def test_saving_the_map_without_a_name_uses_the_active_session(client, node):
    client.post('/api/map/save', json={})
    assert node.saved_maps == ['']


def test_a_failed_map_save_is_a_503_carrying_the_reason(client, node):
    node.save_map_result = {'ok': False, 'result': {}, 'error': 'slam_toolbox not running'}
    response = client.post('/api/map/save', json={'name': 'casa'})
    assert response.status_code == 503
    assert response.json() == {'ok': False, 'error': 'slam_toolbox not running'}


# --- /api/zones/here -------------------------------------------------------

def test_naming_the_room_the_robot_is_in_stores_a_box_around_it():
    """The manual-mapping flow: drive in, name it, carry on."""
    node = FakeNode(pose={'x': 1.0, 'y': -2.0})
    response = TestClient(build_app(node)).post(
        '/api/zones/here', json={'name': ' Cocina ', 'size': 2.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body['name'] == 'cocina'
    assert body['room_type'] == 'kitchen'     # recognized, so it is findable by function
    assert node.zones.load_all()['cocina'] == {
        'x_min': 0.0, 'y_min': -3.0, 'x_max': 2.0, 'y_max': -1.0,
    }
    assert node.indexed_zones == [('cocina', body['area'])]


def test_a_zone_here_is_never_smaller_than_the_robot():
    node = FakeNode(pose={'x': 0.0, 'y': 0.0})
    body = TestClient(build_app(node)).post(
        '/api/zones/here', json={'name': 'cocina', 'size': 0.01},
    ).json()
    assert body['area'] == {'x_min': -0.2, 'y_min': -0.2, 'x_max': 0.2, 'y_max': 0.2}


def test_naming_a_zone_here_is_rejected_when_the_robot_pose_is_unknown(client, node):
    """Before SLAM publishes map->base_link there is no "here" to name."""
    response = client.post('/api/zones/here', json={'name': 'cocina'})
    assert response.status_code == 409
    assert node.zones.load_all() == {}
    assert node.indexed_zones == []


@pytest.mark.parametrize('name', ['', '   '])
def test_naming_a_zone_here_without_a_name_is_rejected(name):
    node = FakeNode(pose={'x': 0.0, 'y': 0.0})
    response = TestClient(build_app(node)).post('/api/zones/here', json={'name': name})
    assert response.status_code == 400
    assert node.zones.load_all() == {}


# --- room types ------------------------------------------------------------

def test_saving_a_zone_reports_the_room_type_it_recognized(client):
    body = client.post('/api/zones', json={
        'name': 'cocina', 'x_min': 0, 'y_min': 0, 'x_max': 1, 'y_max': 1,
    }).json()
    assert body['room_type'] == 'kitchen'


def test_a_zone_name_with_no_room_meaning_reports_none(client):
    body = client.post('/api/zones', json={
        'name': 'estacion_a', 'x_min': 0, 'y_min': 0, 'x_max': 1, 'y_max': 1,
    }).json()
    assert body['room_type'] == ''


def test_room_names_are_offered_for_autocomplete(client):
    names = client.get('/api/room-types').json()['names']
    assert 'cocina' in names and 'dormitorio' in names


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


@pytest.mark.parametrize(('size', 'expected_half'), [(2.0, 1.0), (0.5, 0.25), (0.0, 0.2)])
def test_box_around_centers_on_the_point_and_has_a_floor(size, expected_half):
    box = box_around(1.0, -1.0, size)
    assert box == {
        'x_min': 1.0 - expected_half, 'y_min': -1.0 - expected_half,
        'x_max': 1.0 + expected_half, 'y_max': -1.0 + expected_half,
    }


@pytest.mark.parametrize(('metadata', 'expected'), [
    ({'label': 'kitchen', 'room_zone': 'cocina'}, 'kitchen · cocina'),
    ({'label': 'area'}, 'area'),
    ({'room_zone': 'cocina'}, 'cocina'),
    ({'source': 'environment_rules.md'}, 'environment_rules.md'),
    ({'task_id': '1721'}, '1721'),
    ({}, 'raw-id'),
])
def test_memory_entry_title_uses_whatever_the_entry_knows_about_itself(metadata, expected):
    assert memory_entry_title(metadata, 'raw-id') == expected


def test_format_memory_entry_marks_a_memory_from_another_map_as_stale():
    entry = format_memory_entry(
        {'id': 'x', 'document': 'd', 'score': 0.5, 'metadata': {'map_id': 'old'}}, 'current',
    )
    assert entry['stale'] is True


def test_format_memory_entry_does_not_call_an_untagged_memory_stale():
    """knowledge_base carries no map_id at all; it is valid on every map."""
    entry = format_memory_entry(
        {'id': 'x', 'document': 'd', 'score': 0.5, 'metadata': {'source': 'a.md'}}, 'current',
    )
    assert entry['stale'] is False


@pytest.mark.parametrize('pose', [{}, {'pose_x': 'nope', 'pose_y': None}])
def test_format_memory_entry_reports_no_coordinates_rather_than_fake_ones(pose):
    entry = format_memory_entry({'id': 'x', 'document': 'd', 'metadata': pose}, '')
    assert (entry['x'], entry['y']) == (None, None)


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
    assert top == COLOR_UNKNOWN


def test_render_map_png_dilates_occupied_cells():
    """Walls are 1px at 0.05 m/px and vanish when scaled, so they are dilated."""
    grid = FakeGrid(3, 3, [0, 0, 0, 0, 100, 0, 0, 0, 0])
    image = _decode(render_map_png(grid)).convert('RGB')
    occupied = COLOR_OCCUPIED
    # The single occupied centre cell paints all 9 pixels.
    assert all(image.getpixel((x, y)) == occupied for x in range(3) for y in range(3))


# --- grouping in the memory viewer (ADR-025) -------------------------------

def _entry(entry_id, document, x=None, y=None, map_id='live', score=-1.0, observations=1,
           title=None, stale=False):
    return {
        'id': entry_id, 'title': title or entry_id, 'document': document, 'score': score,
        'label': '', 'zone': '', 'source': '', 'x': x, 'y': y, 'map_id': map_id,
        'observations': observations, 'stale': stale,
    }


def test_facts_about_the_same_spot_become_one_card():
    """A landmark and its seeded description share coordinates on purpose."""
    landmark = _entry('landmark-estacion_a', 'estacion_a at (x=-2.19, y=-1.61)',
                      x=-2.19, y=-1.61, score=0.5, title='estacion_a')
    seeded = _entry('scene-seed-estacion_a', 'area at (x=-2.19, y=-1.61): white, open',
                    x=-2.19, y=-1.61, score=0.7, title='area')
    (card,) = group_memory_entries([landmark, seeded])
    assert card['count'] == 2
    assert card['ids'] == ['landmark-estacion_a', 'scene-seed-estacion_a']
    assert card['title'] == 'estacion_a / area'
    assert card['score'] == 0.7                     # the group ranks by its best member
    assert [f['title'] for f in card['facts']] == ['estacion_a', 'area']


def test_the_same_task_logged_many_times_becomes_one_card():
    runs = [_entry(f'task-{i}', 'Goal: Ve a estacion_a\nOutcome: llegué') for i in range(7)]
    (card,) = group_memory_entries(runs)
    assert card['count'] == 7


def test_different_places_and_different_maps_stay_separate_cards():
    cards = group_memory_entries([
        _entry('scene-a', 'area', x=0.0, y=0.0),
        _entry('scene-b', 'area', x=0.5, y=0.0),                    # another spot
        _entry('scene-c', 'area', x=0.0, y=0.0, map_id='old-map'),  # same spot, dead map
    ])
    assert [c['id'] for c in cards] == ['scene-a', 'scene-b', 'scene-c']


def test_a_group_is_stale_only_if_every_member_is():
    cards = group_memory_entries([
        _entry('task-1', 'Goal: x', stale=True), _entry('task-2', 'Goal: x', stale=False),
    ])
    assert cards[0]['stale'] is False


def test_observation_counts_reach_the_viewer():
    item = {'id': 'scene-a', 'document': 'd', 'score': -1.0,
            'metadata': {'pose_x': 0.0, 'pose_y': 0.0, 'observations': 5}}
    assert format_memory_entry(item, '')['observations'] == 5
    bare = format_memory_entry({'id': 'k', 'document': 'd', 'metadata': {}}, '')
    assert bare['observations'] == 1


# --- the event cursor across a restart of the node (2026-09-17) -------------

from robot_dashboard.dashboard_node import EventBuffer  # noqa: E402


def test_a_cursor_from_a_previous_process_gets_everything():
    """A page left open across a relaunch used to see nothing for ever."""
    buffer = EventBuffer()
    for text in ('a', 'b'):
        buffer.append('status', text)
    assert [e['text'] for e in buffer.since(500)] == ['a', 'b']
    assert [e['text'] for e in buffer.since(1)] == ['b']
    assert buffer.since(2) == []


def test_clearing_the_events_empties_the_thread(client, node):
    """The dashboard's "Limpiar" button, so a recording starts on a clean thread."""
    node.events._events.extend([{'id': 1, 'text': 'a'}, {'id': 2, 'text': 'b'}])
    assert client.post('/api/events/clear').json() == {'ok': True, 'cleared': 2}
    assert client.get('/api/events').json() == {'events': []}


def test_clearing_keeps_ids_increasing_so_pages_are_not_resent_old_events():
    buffer = EventBuffer()
    buffer.append('status', 'a')
    assert buffer.clear() == 1
    buffer.append('status', 'b')
    assert [e['id'] for e in buffer.since(1)] == [2]
