"""Shared ROS 2 helpers for the RAG navigation benchmark.

A single rclpy node that can publish goals, wait for the robot's final
response, read the robot pose (TF) and the SLAM map, call skills directly,
and integrate odometry distance during a task. Used by seed_memory.py and
run_benchmark.py.
"""

from __future__ import annotations

import json
import math
import threading
import time

import rclpy
from nav_msgs.msg import OccupancyGrid, Odometry
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
from robot_interfaces.srv import ExecuteSkill, QueryRAG, UpdateMap

_MAP_QOS = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)


class BenchNode(Node):
    """Drives and observes the robot for benchmarking."""

    def __init__(self) -> None:
        super().__init__('bench_node')
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        self._goal_pub = self.create_publisher(String, '/robot/goal', 10)
        self._skills_client = self.create_client(ExecuteSkill, '/skills/execute')
        self._update_map_client = self.create_client(UpdateMap, '/rag/update_map')
        self._rag_client = self.create_client(QueryRAG, '/rag/query')

        self._response_lock = threading.Lock()
        self._latest_response: str | None = None
        self.create_subscription(String, '/robot/response', self._on_response, 10)

        self._plan_lock = threading.Lock()
        self._latest_plan: str | None = None
        self.create_subscription(String, '/robot/plan', self._on_plan, 10)

        self._latest_map: OccupancyGrid | None = None
        self.create_subscription(OccupancyGrid, '/map', self._on_map, _MAP_QOS)

        self._odom_lock = threading.Lock()
        self._last_odom: tuple[float, float] | None = None
        self._odom_distance = 0.0
        self.create_subscription(Odometry, '/odom', self._on_odom, 20)

    def _on_response(self, msg: String) -> None:
        with self._response_lock:
            self._latest_response = msg.data

    def _on_plan(self, msg: String) -> None:
        with self._plan_lock:
            self._latest_plan = msg.data

    def _on_map(self, msg: OccupancyGrid) -> None:
        self._latest_map = msg

    def _on_odom(self, msg: Odometry) -> None:
        p = (msg.pose.pose.position.x, msg.pose.pose.position.y)
        with self._odom_lock:
            if self._last_odom is not None:
                self._odom_distance += math.dist(p, self._last_odom)
            self._last_odom = p

    # --- pose / map ---

    def get_pose(self) -> tuple[float, float] | None:
        """Returns the robot's (x, y) in the map frame via TF, or None."""
        try:
            tf = self._tf_buffer.lookup_transform('map', 'base_link', rclpy.time.Time())
        except (LookupException, TransformException):
            return None
        return (tf.transform.translation.x, tf.transform.translation.y)

    def get_map(self) -> OccupancyGrid | None:
        """Returns the latest SLAM occupancy grid, or None."""
        return self._latest_map

    # --- odometry distance ---

    def reset_odom_distance(self) -> None:
        """Starts a new odometry distance count (called before each run)."""
        with self._odom_lock:
            self._odom_distance = 0.0
            self._last_odom = None

    def get_odom_distance(self) -> float:
        """Returns the distance travelled, in meters, since the last reset."""
        with self._odom_lock:
            return self._odom_distance

    # --- goals / skills ---

    def publish_goal(self, text: str) -> None:
        """Publishes a natural language goal on /robot/goal."""
        self._goal_pub.publish(String(data=text))

    def wait_for_response(self, timeout_sec: float) -> str | None:
        """Blocks until a new /robot/response arrives or the timeout elapses.

        Clears the previous response first so stale reports are not mistaken
        for this task's completion.
        """
        with self._response_lock:
            self._latest_response = None
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            with self._response_lock:
                if self._latest_response is not None:
                    return self._latest_response
            time.sleep(0.2)
        return None

    def seed_semantic_object(
        self, object_id: str, label: str, x: float, y: float, description: str, zone: str = '',
    ) -> bool:
        """Registers an object at (x, y) in the RAG semantic_map via /rag/update_map."""
        self._update_map_client.wait_for_service(timeout_sec=10.0)
        obj = SemanticObject()
        obj.object_id = object_id
        obj.label = label
        obj.confidence = 1.0
        obj.pose.position.x = float(x)
        obj.pose.position.y = float(y)
        obj.description = description
        obj.room_zone = zone
        obj.timestamp = self.get_clock().now().to_msg()
        future = self._update_map_client.call_async(UpdateMap.Request(object_data=obj))
        done = threading.Event()
        future.add_done_callback(lambda _: done.set())
        if not done.wait(timeout=10.0):
            return False
        return future.result().success

    def query_rag(self, text: str, collection: str, top_k: int = 5) -> list[str]:
        """Retrieves contexts from /rag/query (used by the attribute scorer)."""
        self._rag_client.wait_for_service(timeout_sec=10.0)
        request = QueryRAG.Request(query_text=text, collection_name=collection, top_k=top_k)
        future = self._rag_client.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _: done.set())
        if not done.wait(timeout=15.0):
            return []
        response = future.result()
        return list(response.contexts) if response.success else []

    def wait_for_plan(self, timeout_sec: float) -> dict | None:
        """Blocks until a new /robot/plan arrives, returning the parsed plan dict."""
        with self._plan_lock:
            self._latest_plan = None
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            with self._plan_lock:
                if self._latest_plan is not None:
                    try:
                        return json.loads(self._latest_plan)
                    except json.JSONDecodeError:
                        return {}
            time.sleep(0.2)
        return None

    def call_skill(self, skill_name: str, params: dict, timeout_sec: float = 300.0) -> dict:
        """Calls /skills/execute synchronously and returns the parsed result."""
        self._skills_client.wait_for_service(timeout_sec=10.0)
        request = ExecuteSkill.Request(skill_name=skill_name, params_json=json.dumps(params))
        future = self._skills_client.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _: done.set())
        if not done.wait(timeout=timeout_sec):
            return {'success': False, 'error': 'timeout'}
        response = future.result()
        result = json.loads(response.result_json) if response.result_json else {}
        return {'success': response.success, 'error': response.error_msg, 'result': result}


def spin_in_thread(node: Node) -> SpinHandle:
    """Spins the node on a MultiThreadedExecutor in a daemon thread.

    Returns a handle whose stop() must be called BEFORE rclpy.shutdown():
    tearing down the context while the spin thread is still inside the rcl
    wait aborts the process ("terminate called without an active exception")
    on rclpy Jazzy.
    """
    executor = rclpy.executors.MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    return SpinHandle(executor, thread)


class SpinHandle:
    """Pairs the background executor with its thread for a clean stop()."""

    def __init__(self, executor, thread) -> None:
        self.executor = executor
        self.thread = thread

    def stop(self) -> None:
        """Stops the executor and joins the spin thread before shutdown."""
        self.executor.shutdown(timeout_sec=2.0)
        self.thread.join(timeout=3.0)
