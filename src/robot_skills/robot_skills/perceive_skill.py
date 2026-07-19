"""Perception skill: classical scene description from camera + LIDAR.

The previous implementation asked Qwen2.5-VL to detect objects in the camera
frame; on this simulator's software-rendered images the VLM was unreliable
(see ADR-014), so perception now uses exact sensor statistics instead:
dominant colors from the camera and obstacle clutter from the LIDAR scan.
The resulting description is stored in semantic memory with the robot's
coordinates, which is what makes goals like "go to the white, open room"
resolvable later.
"""

from cv_bridge import CvBridge
from sensor_msgs.msg import Image, LaserScan

from robot_skills.scene_descriptor import describe_scene, dominant_colors


class PerceiveSkill:
    """Builds a scene description from the latest camera frame and LIDAR scan."""

    def __init__(self) -> None:
        self._bridge = CvBridge()

    def describe(self, image_msg: Image | None, scan_msg: LaserScan | None) -> dict:
        """Describes the surroundings from the available sensors.

        Args:
            image_msg: Latest camera frame, or None if not received yet.
            scan_msg: Latest LIDAR scan, or None if not received yet.

        Returns:
            Dict with "colors", clutter metrics, and a "description" string
            (see scene_descriptor.describe_scene).

        Raises:
            RuntimeError: If neither sensor has produced data yet.
        """
        if image_msg is None and scan_msg is None:
            raise RuntimeError('No camera frame or LIDAR scan received yet')
        rgb = (
            self._bridge.imgmsg_to_cv2(image_msg, desired_encoding='rgb8')
            if image_msg is not None else None
        )
        ranges = list(scan_msg.ranges) if scan_msg is not None else None
        return describe_scene(rgb, ranges)

    def sample_colors(self, image_msg: Image | None) -> list[str]:
        """Returns the dominant colors of a single camera frame, or [] if none.

        Used by the scan_360 skill to sample colors at each heading of an
        in-place rotation (see scene_descriptor.describe_scan_360).
        """
        if image_msg is None:
            return []
        rgb = self._bridge.imgmsg_to_cv2(image_msg, desired_encoding='rgb8')
        return dominant_colors(rgb)
