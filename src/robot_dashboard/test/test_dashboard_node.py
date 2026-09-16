"""Node-level tests for dashboard_node: the HTTP <-> ROS bridge.

`tests/test_web_api.py` covers the HTTP layer against a stub. These cover what
a stub cannot: that the real node wires that layer to real ROS entities — a
goal POSTed over HTTP is published as a std_msgs/String on /robot/goal, a WASD
keypress becomes a real velocity message (and stops on its own when the browser
goes quiet), and messages arriving on the robot's topics show up in the event
stream the UI polls.

The HTTP server is bound to a throwaway port, and manual driving to a throwaway
topic, so the test never collides with a dashboard the developer already has
running on 8080 — or drives a robot they have running in Gazebo.

Run with `colcon test --packages-select robot_dashboard`.
"""

import socket
import threading
import time

import pytest
import rclpy
from geometry_msgs.msg import TwistStamped
from rclpy.executors import MultiThreadedExecutor, SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String

from robot_interfaces.srv import UpdateMap

from robot_dashboard.dashboard_node import DashboardNode


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


HTTP_PORT = _free_port()
# Never the real /cmd_vel: a developer running `colcon test` with the simulator
# up must not have their robot driven by the test suite.
CMD_VEL_TOPIC = '/dashboard_test_cmd_vel'
# Short enough to watch the deadman fire without a slow test.
TELEOP_TIMEOUT_SEC = 0.3


@pytest.fixture(scope='module')
def ros():
    # Global parameter overrides: DashboardNode takes no constructor arguments,
    # so this is how the port and the teleop settings are injected without
    # touching production code.
    rclpy.init(args=[
        '--ros-args',
        '-p', f'http_port:={HTTP_PORT}',
        '-p', f'cmd_vel_topic:={CMD_VEL_TOPIC}',
        '-p', f'teleop_timeout_sec:={TELEOP_TIMEOUT_SEC}',
    ])
    yield
    rclpy.shutdown()


@pytest.fixture(scope='module')
def node(ros):
    node = DashboardNode()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    threading.Thread(target=executor.spin, daemon=True).start()
    yield node
    executor.shutdown()
    node.destroy_node()


@pytest.fixture(scope='module')
def client(node):
    """An HTTP client pointed at the node's real uvicorn server."""
    import httpx

    base = f'http://127.0.0.1:{HTTP_PORT}'
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            httpx.get(f'{base}/api/events', timeout=1.0)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        pytest.fail('the dashboard HTTP server never came up')
    with httpx.Client(base_url=base, timeout=10.0) as client:
        yield client


@pytest.fixture(scope='module')
def listener(ros):
    """Subscribes to /robot/goal to observe what the dashboard actually publishes."""
    node = Node('dashboard_test_listener')
    received: list[str] = []
    node.create_subscription(String, '/robot/goal', lambda m: received.append(m.data), 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    threading.Thread(target=executor.spin, daemon=True).start()
    yield received
    executor.shutdown()
    node.destroy_node()


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_the_server_is_actually_listening(client):
    assert client.get('/').status_code == 200


def test_posting_a_goal_publishes_it_on_the_robot_goal_topic(client, listener):
    """The bridge that matters: a browser POST becomes a real ROS message."""
    goal = 've a la cocina y dime que ves'
    assert client.post('/api/goal', json={'text': goal}).status_code == 200
    assert _wait_for(lambda: goal in listener), (
        f'goal never reached /robot/goal; received: {listener}'
    )


def test_a_rejected_goal_is_never_published(client, listener):
    before = len(listener)
    assert client.post('/api/goal', json={'text': '   '}).status_code == 400
    time.sleep(1.0)
    assert len(listener) == before


def test_messages_on_robot_topics_reach_the_event_stream(node, client):
    """Whatever the agent publishes must become visible in the UI timeline."""
    publisher = node.create_publisher(String, '/robot/response', 10)
    publisher.publish(String(data='he llegado a la cocina'))

    def response_arrived():
        events = client.get('/api/events').json()['events']
        return any(
            e['type'] == 'response' and e['text'] == 'he llegado a la cocina'
            for e in events
        )

    assert _wait_for(response_arrived), 'response never appeared in /api/events'


def test_event_ids_increase_so_the_ui_can_poll_incrementally(client, listener):
    """The UI polls with ?since=<last id>; ids must be monotonic for that to work."""
    client.post('/api/goal', json={'text': 'primera orden'})
    assert _wait_for(lambda: client.get('/api/events').json()['events'])

    events = client.get('/api/events').json()['events']
    ids = [e['id'] for e in events]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids), 'event ids must be unique'

    last_id = ids[-1]
    client.post('/api/goal', json={'text': 'segunda orden'})
    assert _wait_for(
        lambda: any(
            e['text'] == 'segunda orden'
            for e in client.get('/api/events', params={'since': last_id}).json()['events']
        ),
    )
    # And nothing already seen is replayed.
    fresh = client.get('/api/events', params={'since': last_id}).json()['events']
    assert all(e['id'] > last_id for e in fresh)


def test_a_zone_saved_over_http_persists_in_the_shared_store(client, node):
    """dashboard writes, skills/planner read — same SQLite file (ADR-011)."""
    response = client.post('/api/zones', json={
        'name': 'Cocina Test', 'x_min': 1.0, 'y_min': 2.0, 'x_max': 0.0, 'y_max': 0.0,
    })
    assert response.status_code == 200
    assert response.json()['name'] == 'cocina_test'
    stored = node.zones.load_all()['cocina_test']
    assert stored == {'x_min': 0.0, 'y_min': 0.0, 'x_max': 1.0, 'y_max': 2.0}

    assert client.delete('/api/zones/cocina_test').status_code == 200
    assert 'cocina_test' not in node.zones.load_all()


def test_map_endpoint_is_serviceable_before_slam_publishes(client):
    """The UI loads long before SLAM exists; this must not 500."""
    body = client.get('/api/map').json()
    assert body['map'] is None
    assert 'zones' in body


def test_saving_a_zone_does_not_block_when_rag_is_down(client, node):
    """Fire-and-forget: no rag_node runs in this test, so /rag/update_map is
    never available. The save must still return promptly (SQLite write only) —
    it must not stall on the RAG service. Regression guard for the 2 s
    wait_for_service that used to block the HTTP handler.
    """
    start = time.monotonic()
    response = client.post('/api/zones', json={
        'name': 'sin_rag', 'x_min': 0.0, 'y_min': 0.0, 'x_max': 1.0, 'y_max': 1.0,
    })
    elapsed = time.monotonic() - start
    assert response.status_code == 200
    assert elapsed < 1.0, f'zone save took {elapsed:.2f}s with RAG down — should be instant'
    assert 'sin_rag' in node.zones.load_all()
    client.delete('/api/zones/sin_rag')


@pytest.fixture(scope='module')
def cmd_vel(ros):
    """Subscribes to the teleop topic to observe what manual driving publishes."""
    node = Node('dashboard_test_cmd_vel_listener')
    received: list[TwistStamped] = []
    node.create_subscription(TwistStamped, CMD_VEL_TOPIC, lambda m: received.append(m), 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    threading.Thread(target=executor.spin, daemon=True).start()
    yield received
    executor.shutdown()
    node.destroy_node()


def test_holding_a_key_in_the_browser_moves_the_real_robot(client, cmd_vel):
    """The bridge that matters for mapping: a keypress becomes a velocity message."""
    cmd_vel.clear()
    body = client.post('/api/teleop', json={'keys': ['w']}).json()
    assert body['driving'] is True and body['linear'] > 0.0
    assert _wait_for(lambda: any(m.twist.linear.x > 0.0 for m in cmd_vel)), (
        'no forward velocity was published'
    )
    client.post('/api/teleop/stop')
    assert _wait_for(lambda: any(m.twist.linear.x == 0.0 for m in cmd_vel))


def test_a_browser_that_goes_quiet_stops_the_robot_by_itself(client, cmd_vel):
    """The deadman (ADR-023): no refresh, no motion — whatever the browser did."""
    client.post('/api/teleop', json={'keys': ['w']})
    assert _wait_for(lambda: any(m.twist.linear.x > 0.0 for m in cmd_vel))
    cmd_vel.clear()
    # Nothing else is sent: the tab "closed". Within the deadman window the node
    # must publish a stop on its own.
    assert _wait_for(
        lambda: any(m.twist.linear.x == 0.0 for m in cmd_vel),
        timeout=TELEOP_TIMEOUT_SEC + 3.0,
    ), 'the robot was never stopped after the browser went silent'
    # ...and it must then fall silent, leaving the topic to Nav2.
    time.sleep(1.0)
    cmd_vel.clear()
    time.sleep(1.0)
    assert cmd_vel == [], 'the dashboard keeps publishing while nobody is driving'


def test_the_memory_panel_degrades_instead_of_hanging_when_rag_is_down(client):
    """No rag_node runs in this test, which is also the state at every launch."""
    start = time.monotonic()
    body = client.get('/api/memory').json()
    elapsed = time.monotonic() - start
    assert body['ok'] is False
    assert body['entries'] == []
    assert '/rag/inspect' in body['error']
    assert elapsed < 2.0, f'the memory panel blocked for {elapsed:.2f}s with rag_node down'


def test_saving_the_map_reports_the_missing_skill_instead_of_hanging(client):
    """Same for the agent stack: a 503 with a reason, not a stalled browser."""
    start = time.monotonic()
    response = client.post('/api/map/save', json={'name': 'casa'})
    elapsed = time.monotonic() - start
    assert response.status_code == 503
    assert '/skills/execute' in response.json()['error']
    assert elapsed < 2.0, f'the map save blocked for {elapsed:.2f}s with the skills node down'


def test_zones_are_reindexed_into_memory_once_rag_node_appears(node):
    """Zones live in SQLite with no map session, but their memory documents are
    session-tagged. Start on another session — loading a saved map pins its id —
    and every zone would silently drop out of retrieval. So the dashboard indexes
    every stored zone again as soon as /rag/update_map exists (ADR-026).

    Runs last in this module: until here no rag service exists, which is what the
    "saving a zone does not block when rag is down" test relies on.
    """
    node.zones.save('cocina_reindex', {'x_min': 0.0, 'y_min': 0.0, 'x_max': 1.0, 'y_max': 1.0})
    received: list[str] = []

    def handle(request, response):
        received.append(request.object_data.object_id)
        response.success = True
        return response

    stub = Node('fake_rag_node')
    stub.create_service(UpdateMap, '/rag/update_map', handle)
    executor = SingleThreadedExecutor()
    executor.add_node(stub)
    threading.Thread(target=executor.spin, daemon=True).start()
    try:
        assert _wait_for(lambda: 'zone-cocina_reindex' in received, timeout=20.0), (
            f'zone was never re-indexed; update_map received: {received}'
        )
    finally:
        executor.shutdown()
        stub.destroy_node()
        node.zones.delete('cocina_reindex')
