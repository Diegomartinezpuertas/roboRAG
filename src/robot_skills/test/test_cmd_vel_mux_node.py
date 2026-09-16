"""Node-level test for cmd_vel_mux_node: real topics, real arbitration (ADR-029).

Topics are overridden so the test never drives a robot the developer has
running in Gazebo.
"""

import threading
import time

import pytest
import rclpy
from geometry_msgs.msg import TwistStamped
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile
from std_msgs.msg import String

TELEOP, NAV, OUT = '/mux_test_teleop', '/mux_test_nav', '/mux_test_out'


@pytest.fixture(scope='module')
def ros():
    rclpy.init(args=[
        '--ros-args', '-p', f'teleop_topic:={TELEOP}', '-p', f'nav_topic:={NAV}',
        '-p', f'output_topic:={OUT}',
    ])
    yield
    rclpy.shutdown()


@pytest.fixture(scope='module')
def harness(ros):
    from robot_skills.cmd_vel_mux_node import CmdVelMuxNode

    mux = CmdVelMuxNode()
    probe = Node('mux_test_probe')
    received, sources = [], []
    probe.create_subscription(TwistStamped, OUT, lambda m: received.append(m.twist.linear.x), 50)
    probe.create_subscription(
        String, '/robot/cmd_vel_source', lambda m: sources.append(m.data),
        QoSProfile(depth=5, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL),
    )
    teleop = probe.create_publisher(TwistStamped, TELEOP, 10)
    nav = probe.create_publisher(TwistStamped, NAV, 10)
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(mux)
    executor.add_node(probe)
    threading.Thread(target=executor.spin, daemon=True).start()
    time.sleep(1.5)                                     # discovery
    yield received, sources, teleop, nav
    executor.shutdown()
    mux.destroy_node()
    probe.destroy_node()


def _twist(x):
    msg = TwistStamped()
    msg.twist.linear.x = x
    return msg


def _publish_for(publisher, value, seconds, rate_hz=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        publisher.publish(_twist(value))
        time.sleep(1.0 / rate_hz)


def test_manual_driving_overrides_nav2_and_hands_control_back(harness):
    received, sources, teleop, nav = harness

    stop = threading.Event()

    def nav2_driving():                                  # Nav2 keeps driving at 0.1
        while not stop.is_set():
            nav.publish(_twist(0.1))
            time.sleep(0.05)

    worker = threading.Thread(target=nav2_driving, daemon=True)
    worker.start()
    try:
        time.sleep(1.0)
        assert received and set(received) == {0.1}, 'Nav2 alone should pass through'

        received.clear()
        _publish_for(teleop, 0.5, seconds=1.0)          # a person takes over
        assert 0.5 in received
        tail = received[len(received) // 2:]
        assert set(tail) == {0.5}, f'Nav2 leaked while manual driving held control: {tail}'

        received.clear()
        time.sleep(1.5)                                  # let go: > teleop timeout
        assert received and set(received[-5:]) == {0.1}, 'control never returned to Nav2'
    finally:
        stop.set()
        worker.join()

    assert 'teleop' in sources and sources[-1] in ('nav2', 'idle')
