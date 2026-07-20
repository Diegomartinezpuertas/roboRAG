"""Makes the ROS packages' pure-Python modules importable without a ROS install.

Each package's code lives at src/<pkg>/<pkg>/*.py, so adding src/<pkg> to
sys.path lets `import <pkg>.<module>` resolve. Only modules that avoid rclpy /
ROS-message imports (guarded under TYPE_CHECKING where needed) are exercised
by this suite; the rest are covered by `colcon test` in the ROS CI job.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / 'src'
for _pkg in ('robot_rag', 'robot_brain', 'robot_zones', 'robot_skills', 'robot_dashboard'):
    sys.path.insert(0, str(_SRC / _pkg))

# eval/scoring.py is pure logic too (the benchmark plan scorer), and is
# deliberately kept free of ROS imports so it can be covered here.
sys.path.insert(0, str(_ROOT / 'eval'))
