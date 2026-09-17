"""Navigation skill wrapping Nav2's SimpleCommander API."""

import math
import time

from geometry_msgs.msg import PoseStamped
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult

from robot_skills.localization import localizer_node


class NavSkill:
    """Drives the robot to an explicit (x, y, theta) pose via Nav2.

    Named zones are resolved to coordinates upstream in skills_executor_node
    (against the SQLite zone store), so this skill only ever deals with
    concrete map-frame poses.

    Owns a BasicNavigator, which is itself a ROS 2 node that self-spins while
    waiting for action results.
    """

    def __init__(self) -> None:
        self.navigator = BasicNavigator()

    def wait_until_active(self) -> None:
        """Blocks until the localizer and bt_navigator report active.

        The localizer is SLAM Toolbox on a live map (ADR-004) and AMCL on a
        saved map loaded read-only (ADR-035), found by name in the graph.
        SimpleCommander's own AMCL wait is not used: it publishes an initial
        pose at the origin until AMCL answers, which would reset a robot that
        was driven before its first skill. AMCL takes its initial pose from
        its parameters instead.
        """
        localizer = localizer_node(self.navigator.get_node_names())
        while localizer is None:
            self.navigator.info('Waiting for a localizer (amcl or slam_toolbox) to appear')
            time.sleep(1.0)
            localizer = localizer_node(self.navigator.get_node_names())
        self.navigator._waitForNodeToActivate(localizer)   # public wait would reset AMCL's pose
        # 'robot_localization' makes waitUntilNav2Active skip the localizer
        # (already waited for above) and wait for bt_navigator only.
        self.navigator.waitUntilNav2Active(localizer='robot_localization')

    def navigate(self, params: dict) -> dict:
        """Navigates to an explicit x/y/theta pose in the map frame.

        Args:
            params: {"x": float, "y": float, "theta": float (optional)}.

        Returns:
            Dict with "reached" (bool) and "message" (str).
        """
        x = float(params['x'])
        y = float(params['y'])
        theta = float(params.get('theta', 0.0))

        goal = PoseStamped()
        goal.header.frame_id = 'map'
        goal.header.stamp = self.navigator.get_clock().now().to_msg()
        goal.pose.position.x = x
        goal.pose.position.y = y
        goal.pose.orientation.z = math.sin(theta / 2.0)
        goal.pose.orientation.w = math.cos(theta / 2.0)

        self.navigator.goToPose(goal)
        while not self.navigator.isTaskComplete():
            # isTaskComplete() already blocks ~0.1s internally when idle, but
            # a bare `pass` still re-acquires the GIL as fast as possible on
            # every return, which can starve other threads on this process's
            # MultiThreadedExecutor (observed: /camera/image_raw callback
            # never firing for the whole duration of a navigate/explore call).
            time.sleep(0.05)

        result = self.navigator.getResult()
        if result == TaskResult.SUCCEEDED:
            return {'reached': True, 'message': f'Reached ({x}, {y})'}
        return {'reached': False, 'message': f'Navigation to ({x}, {y}) failed: {result}'}

    def spin(self, angle: float, time_allowance: int = 15) -> bool:
        """Rotates the robot in place via Nav2's Spin behavior.

        Args:
            angle: Relative rotation in radians (positive = counter-clockwise).
            time_allowance: Max seconds Nav2 gives the behavior before aborting.

        Returns:
            True if the rotation completed successfully.
        """
        if not self.navigator.spin(spin_dist=angle, time_allowance=time_allowance):
            return False
        while not self.navigator.isTaskComplete():
            time.sleep(0.05)
        return self.navigator.getResult() == TaskResult.SUCCEEDED
