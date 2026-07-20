"""Guards the node shutdown contract (ADR-016) for every long-running node.

Before that ADR, each node's `main()` caught only `KeyboardInterrupt`. Under
`ros2 launch` the signal surfaces as `ExternalShutdownException`, which escaped
— and the `finally` block then called `rclpy.shutdown()` on an already-torn-down
context, raising `RCLError` on top. Every stop of the stack printed two
tracebacks per node. With `respawn=True` on all agent nodes, that made a normal
shutdown indistinguishable in the log from the crash respawn exists to catch.

This test spawns each node's installed executable, sends SIGINT, and asserts it
exits cleanly and silently. Nodes are launched as their installed executables
rather than via `ros2 run`, because that is what `ros2 launch` does — and
because the `ros2 run` wrapper does not forward a programmatic SIGINT to its
child, which would make this test vacuous.

`rag_node` is excluded on purpose: it ingests the knowledge base on startup,
which requires a running Ollama server, so it cannot be started in a bare test
environment. It follows the identical `main()` contract.

Run with `colcon test --packages-select robot_bringup`.
"""

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

# (package, executable) for every node that can start without a simulator,
# an LLM server, or a populated map.
NODES = [
    ('robot_dashboard', 'dashboard_node'),
    ('robot_skills', 'skills_executor_node'),
    ('robot_brain', 'llm_planner_node'),
]

STARTUP_GRACE_SEC = 12.0
SHUTDOWN_TIMEOUT_SEC = 15.0


def _executable(package: str, name: str) -> Path:
    """Resolves an installed node executable from the sourced workspace.

    AMENT_PREFIX_PATH is the reliable anchor: it is set by `install/setup.bash`
    and points at each package's install prefix, so this works regardless of
    where the workspace lives (ADR-015).
    """
    for prefix in os.environ.get('AMENT_PREFIX_PATH', '').split(':'):
        if not prefix:
            continue
        candidate = Path(prefix) / 'lib' / package / name
        if candidate.is_file():
            return candidate
    pytest.skip(f'{package}/{name} not found on AMENT_PREFIX_PATH; workspace not sourced')
    raise AssertionError('unreachable')


@pytest.mark.parametrize(('package', 'name'), NODES, ids=[n for _, n in NODES])
def test_node_exits_cleanly_and_silently_on_sigint(package, name, tmp_path):
    executable = _executable(package, name)

    env = dict(os.environ)
    # Isolate any data the node writes (zones.db, logs) from the real workspace.
    env['ROBOT_WS'] = str(tmp_path)
    (tmp_path / 'data').mkdir(parents=True, exist_ok=True)
    if name == 'dashboard_node':
        # Avoid colliding with a dashboard already running on the default port.
        env['ROS_ARGS'] = ''

    args = [str(executable)]
    if name == 'dashboard_node':
        args += ['--ros-args', '-p', 'http_port:=0']

    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    try:
        time.sleep(STARTUP_GRACE_SEC)
        assert process.poll() is None, (
            f'{name} exited on its own before the signal:\n{process.communicate()[0]}'
        )

        process.send_signal(signal.SIGINT)
        try:
            output = process.communicate(timeout=SHUTDOWN_TIMEOUT_SEC)[0]
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            pytest.fail(f'{name} did not exit within {SHUTDOWN_TIMEOUT_SEC}s of SIGINT')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 0, (
        f'{name} exited with {process.returncode} on SIGINT; expected a clean 0.\n{output}'
    )
    assert 'Traceback' not in output, (
        f'{name} printed a traceback on an ordinary shutdown:\n{output}'
    )
    # The specific regression ADR-016 fixed.
    assert 'ExternalShutdownException' not in output
    assert 'rcl_shutdown already called' not in output
