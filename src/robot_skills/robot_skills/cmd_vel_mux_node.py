"""ROS 2 node that gives /cmd_vel a single owner: manual driving over Nav2."""

import time

import rclpy
from geometry_msgs.msg import TwistStamped
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile
from std_msgs.msg import String

from robot_skills.cmd_vel_mux import CmdVelMux, MuxSource

# The active source is state, not an event: a dashboard that connects late must
# still learn who is driving, so the last value is kept for late subscribers.
SOURCE_QOS = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)


class CmdVelMuxNode(Node):
    """ROS 2 node arbitrating velocity commands between manual driving and Nav2.

    Subscribes:
        /robot/cmd_vel_manual (geometry_msgs/TwistStamped): Manual driving (dashboard WASD).
            Not /cmd_vel_teleop: Nav2's behavior_server already subscribes to that
            name as the input of its AssistedTeleop behavior.
        /cmd_vel_nav_out (geometry_msgs/TwistStamped): Nav2's final command, after
            its velocity smoother and collision monitor.

    Publishes:
        /cmd_vel (geometry_msgs/TwistStamped): The command of whichever source holds
            control; the only publisher the robot base listens to.
        /robot/cmd_vel_source (std_msgs/String): "teleop", "nav2" or "idle", on change
            (transient local, so late subscribers get the current owner).

    Parameters:
        teleop_topic (str): Manual driving input. Default: /robot/cmd_vel_manual
        nav_topic (str): Nav2 input. Default: /cmd_vel_nav_out
        output_topic (str): Command sent to the robot. Default: /cmd_vel
        teleop_timeout_sec (float): Manual control is held this long after the last
            manual command. Default: 0.5
        nav_timeout_sec (float): Same for Nav2. Default: 0.5
    """

    def __init__(self) -> None:
        super().__init__('cmd_vel_mux_node')
        self.declare_parameter('teleop_topic', '/robot/cmd_vel_manual')
        self.declare_parameter('nav_topic', '/cmd_vel_nav_out')
        self.declare_parameter('output_topic', '/cmd_vel')
        self.declare_parameter('teleop_timeout_sec', 0.5)
        self.declare_parameter('nav_timeout_sec', 0.5)

        self._mux = CmdVelMux([
            MuxSource('teleop', priority=2,
                      timeout_sec=self.get_parameter('teleop_timeout_sec').value),
            MuxSource('nav2', priority=1,
                      timeout_sec=self.get_parameter('nav_timeout_sec').value),
        ])
        self._reported = None
        self._out = self.create_publisher(
            TwistStamped, self.get_parameter('output_topic').value, 10,
        )
        self._source_pub = self.create_publisher(String, '/robot/cmd_vel_source', SOURCE_QOS)
        self.create_subscription(
            TwistStamped, self.get_parameter('teleop_topic').value,
            lambda msg: self._on_command('teleop', msg), 10,
        )
        self.create_subscription(
            TwistStamped, self.get_parameter('nav_topic').value,
            lambda msg: self._on_command('nav2', msg), 10,
        )
        # Reports the hand-back to "idle"/"nav2" even when no message arrives to
        # trigger it — the moment manual control lapses is exactly when nothing
        # new comes in from the teleop side.
        self.create_timer(0.2, self._report_source)
        self._report_source()
        self.get_logger().info('cmd_vel_mux_node ready (teleop > nav2)')

    def _on_command(self, source: str, msg: TwistStamped) -> None:
        if self._mux.offer(source, time.monotonic()):
            self._out.publish(msg)
        self._report_source()

    def _report_source(self) -> None:
        active = self._mux.active(time.monotonic()) or 'idle'
        if active != self._reported:
            self._reported = active
            self._source_pub.publish(String(data=active))
            if active == 'teleop':
                self.get_logger().info('Manual driving took control of /cmd_vel')


def main(args: list[str] | None = None) -> None:
    """Entry point for the cmd_vel_mux_node executable."""
    rclpy.init(args=args)
    node = CmdVelMuxNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        # How rclpy reports SIGINT/SIGTERM from `ros2 launch` — a stop, not a crash.
        pass
    finally:
        node.destroy_node()
        # Guarded: on external shutdown the context is already down (ADR-016).
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
