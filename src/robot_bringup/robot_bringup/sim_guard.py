"""Refuse to start a simulation while another Gazebo server runs — no ROS imports.

Two `gz sim` servers in one Gazebo partition publish on the same gz-transport
topics. The ros_gz bridge then forwards both robots' scans, odometry and clock,
and SLAM Toolbox draws a second, rotated copy of the house into the map (seen
2026-09-17 on the saved map "house").

The second server is usually an orphan. Closing the terminal of an earlier
launch (SIGHUP) stops `ros2 launch` and the `/bin/sh -c` wrapper it starts
Gazebo through, but the `ruby gz sim` server keeps running with nothing left
to stop it. Ctrl+C reaches the server and does not leave one behind (both
reproduced). Kept free of launch/rclpy imports so it is unit-tested in layer 1
(ADR-018).

See docs/decisions/ADR-034-refuse-a-second-gazebo.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

PARTITION_VAR = 'GZ_PARTITION'


def is_gazebo_server(argv: list[str]) -> bool:
    """Tells whether a process's arguments are a Gazebo server.

    `gz` is a ruby script that rewrites its process title, so /proc holds either
    separate arguments or a single "gz sim -r -s …" string; both are accepted,
    as is an explicit `ruby …/gz sim …`. The shell wrapper launch files start it
    through (`/bin/sh -c "gz sim …"`) is not the server, and `gz sim -g` is the
    GUI client alone. `gz sim` without -s or -g runs a server with its GUI.

    Args:
        argv: The process arguments, as split from /proc/<pid>/cmdline.

    Returns:
        True if the process runs a Gazebo simulation server.
    """
    tokens = ' '.join(argv).split()
    if tokens and Path(tokens[0]).name.startswith('ruby'):
        tokens = tokens[1:]
    if len(tokens) < 2 or Path(tokens[0]).name != 'gz' or tokens[1] != 'sim':
        return False
    return '-g' not in tokens[2:]


def gazebo_partition(environ: Mapping[str, str]) -> str:
    """The Gazebo transport partition an environment selects ('' when unset: the default one).

    Args:
        environ: Environment variables, e.g. os.environ.

    Returns:
        The GZ_PARTITION value, or '' when it is not set.
    """
    return environ.get(PARTITION_VAR, '')


def _read_argv(proc_dir: Path) -> list[str]:
    raw = (proc_dir / 'cmdline').read_bytes()
    return [arg.decode(errors='replace') for arg in raw.split(b'\0') if arg]


def _read_environ(proc_dir: Path) -> dict[str, str]:
    raw = (proc_dir / 'environ').read_bytes()
    pairs = (item.decode(errors='replace').partition('=') for item in raw.split(b'\0') if item)
    return {key: value for key, _, value in pairs}


def running_gazebo_servers(partition: str, proc_root: str | Path = '/proc') -> list[int]:
    """Finds the Gazebo servers already running in a partition.

    A process whose environment cannot be read belongs to another user, whose
    default partition differs from this user's, so it is not counted. A
    process that exits during the scan is skipped.

    Args:
        partition: The partition to look in, from gazebo_partition().
        proc_root: The proc filesystem to scan.

    Returns:
        The servers' PIDs, ascending.
    """
    pids = []
    for entry in Path(proc_root).iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if not is_gazebo_server(_read_argv(entry)):
                continue
            if gazebo_partition(_read_environ(entry)) != partition:
                continue
        except OSError:
            continue
        pids.append(int(entry.name))
    return sorted(pids)


def second_simulation_error(pids: list[int]) -> str:
    """The message a launch fails with when Gazebo servers are already running.

    Args:
        pids: PIDs from running_gazebo_servers().

    Returns:
        What is wrong, why it matters and how to fix it, in one message.
    """
    listed = ' '.join(str(pid) for pid in pids)
    return (
        f'A Gazebo server is already running (PID {listed}). A second simulation would feed '
        'its robot\'s scans, odometry and clock into this SLAM and corrupt the map. '
        'Stop that simulation first: Ctrl+C its launch, or, if that terminal is already '
        f'gone (a closed terminal leaves the server behind), run: kill {listed}. '
        'To run two simulations on purpose, give this one its own GZ_PARTITION and ROS_DOMAIN_ID.'
    )
