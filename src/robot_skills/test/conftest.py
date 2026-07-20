"""Isolates the ROS node tests from the developer's real data directory.

`skills_executor_node` builds its default paths from ROBOT_WS at import time
(ADR-015), so this must be set before the node module is imported — which is
exactly what a conftest at this level guarantees.
"""

import os
import tempfile

_TMP_WS = tempfile.mkdtemp(prefix='robot_skills_test_ws_')
os.environ['ROBOT_WS'] = _TMP_WS
