"""How much of the house an explore run mapped — the measurement behind ADR-027.

Reads the live SLAM map from the dashboard (the same image it shows) and reports
the known area — every cell that is not unexplored — and the map's extent. Run it
before and after an `explore` call, on a fresh simulation each time, to compare
exploration strategies:

    ros2 launch robot_bringup full_system.launch.py use_rviz:=false
    ros2 param set /skills_executor_node explore_strategy nearest   # or clusters
    python3 exploration_coverage.py
    ros2 service call /skills/execute robot_interfaces/srv/ExecuteSkill \\
      "{skill_name: 'explore', params_json: '{\\"duration_sec\\": 240}'}"
    python3 exploration_coverage.py

No ROS imports: it only talks to the dashboard over HTTP.

Usage: python3 exploration_coverage.py [--url http://localhost:8080]
"""

import argparse
import io
import json
import sys
import urllib.request
from pathlib import Path

from PIL import Image

# robot_dashboard renders unexplored cells in this colour; importing the
# constant keeps the measurement tied to the renderer.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src' / 'robot_dashboard'))

from robot_dashboard.web_api import COLOR_UNKNOWN  # noqa: E402


def known_area(png_bytes: bytes, resolution: float) -> float:
    """Known area in m²: every rendered cell that is not the unexplored colour.

    Args:
        png_bytes: The map image from /api/map/png (one pixel per grid cell).
        resolution: Meters per cell.

    Returns:
        Area in square meters.
    """
    image = Image.open(io.BytesIO(png_bytes)).convert('RGB')
    cells = image.width * image.height
    counts = {color: count for count, color in image.getcolors(maxcolors=cells)}
    unknown = counts.get(COLOR_UNKNOWN, 0)
    return (cells - unknown) * resolution * resolution


def main() -> None:
    """Prints the live map's known area and extent."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--url', default='http://localhost:8080')
    url = parser.parse_args().url.rstrip('/')
    meta = json.load(urllib.request.urlopen(f'{url}/api/map'))['map']
    if meta is None:
        raise SystemExit('No map yet: SLAM has not published one.')
    png = urllib.request.urlopen(f'{url}/api/map/png').read()
    res = meta['resolution']
    print(f'known {known_area(png, res):.1f} m² | extent '
          f'{meta["width"] * res:.1f} x {meta["height"] * res:.1f} m')


if __name__ == '__main__':
    main()
