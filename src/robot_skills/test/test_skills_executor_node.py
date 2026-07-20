"""Node-level tests for skills_executor_node: real service wiring and error paths.

Unlike the pure-logic suite in `tests/`, these instantiate the actual ROS node,
spin it on a MultiThreadedExecutor, and call `/skills/execute` over a real
service client — so they cover the wiring the pure-logic tests cannot see:
that the service is advertised under the right name, that the request/response
types round-trip, and that a failing skill produces a well-formed error
response instead of taking the node down.

Deliberately limited to paths that need no simulator: dispatch, parameter
validation, and zone resolution. Anything that drives the robot needs Nav2 and
Gazebo and stays a manual check.

Run with `colcon test --packages-select robot_skills`.
"""

import json
import threading

import pytest
import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from robot_interfaces.srv import ExecuteSkill
from robot_zones.zone_store import ZoneStore

from robot_skills.skills_executor_node import WS_ROOT, SkillsExecutorNode

SERVICE = '/skills/execute'


@pytest.fixture(scope='module')
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture(scope='module')
def executor_node(ros):
    """The real node, spinning on its own executor thread for the whole module."""
    node = SkillsExecutorNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    yield node
    executor.shutdown()
    node.destroy_node()


@pytest.fixture(scope='module')
def client(ros, executor_node):
    """A real service client, so requests cross the middleware like production."""
    node = Node('skills_test_client')
    client = node.create_client(ExecuteSkill, SERVICE)
    assert client.wait_for_service(timeout_sec=10.0), f'{SERVICE} was never advertised'
    yield _Caller(node, client)
    node.destroy_node()


class _Caller:
    """Calls the service synchronously from the test thread."""

    def __init__(self, node, client):
        self._node = node
        self._client = client

    def call(self, skill_name, params=None, params_json=None, timeout=15.0):
        request = ExecuteSkill.Request(
            skill_name=skill_name,
            params_json=params_json if params_json is not None else json.dumps(params or {}),
        )
        future = self._client.call_async(request)
        rclpy.spin_until_future_complete(self._node, future, timeout_sec=timeout)
        response = future.result()
        assert response is not None, f'no response from {SERVICE} within {timeout}s'
        return response


def test_service_is_advertised(client):
    """The wiring itself: a client can discover and reach /skills/execute."""
    # Reaching the fixture at all proves discovery; a trivial call proves the
    # request/response types round-trip.
    response = client.call('definitely_not_a_skill')
    assert response.result_json == '{}'


def test_unknown_skill_reports_a_clean_failure(client):
    response = client.call('fly')
    assert response.success is False
    assert 'Unknown skill' in response.error_msg
    assert 'fly' in response.error_msg


def test_malformed_params_json_is_rejected_without_crashing(client):
    response = client.call('perceive', params_json='{not valid json')
    assert response.success is False
    assert 'Invalid params_json' in response.error_msg


def test_navigate_to_an_unknown_zone_names_the_known_ones(client, executor_node):
    """The planner may emit a stale zone name; the error must be actionable."""
    ZoneStore(str(WS_ROOT / 'data' / 'zones.db')).save(
        'cocina', {'x_min': 0.0, 'y_min': 0.0, 'x_max': 1.0, 'y_max': 1.0},
    )
    response = client.call('navigate', {'zone': 'garaje'})
    assert response.success is False
    assert 'Unknown zone: garaje' in response.error_msg
    assert 'cocina' in response.error_msg, 'the error should list what IS known'


def test_explore_in_an_unknown_zone_is_rejected_before_moving(client):
    response = client.call('explore', {'zone': 'inexistente', 'duration_sec': 1})
    assert response.success is False
    assert 'Unknown zone' in response.error_msg


def test_the_node_survives_a_failed_call_and_still_serves(client):
    """A skill raising must not poison the executor — the regression that matters."""
    assert client.call('nope').success is False
    response = client.call('also_nope')
    assert response.success is False
    assert 'Unknown skill' in response.error_msg
