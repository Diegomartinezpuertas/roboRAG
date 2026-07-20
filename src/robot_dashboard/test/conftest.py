"""Isolates the ROS node tests from the developer's real data directory.

`dashboard_node` builds its default zones_db path from ROBOT_WS at import time
(ADR-015), so this must be set before the node module is imported — which is
exactly what a conftest at this level guarantees.
"""

import os
import tempfile

_TMP_WS = tempfile.mkdtemp(prefix='robot_dashboard_test_ws_')
os.environ['ROBOT_WS'] = _TMP_WS
