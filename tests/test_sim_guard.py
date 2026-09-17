"""Tests for refusing a second Gazebo simulation (ADR-034)."""

import pytest

from robot_bringup.sim_guard import (
    gazebo_partition,
    is_gazebo_server,
    running_gazebo_servers,
    second_simulation_error,
)

# The shapes /proc showed on 2026-09-17: ruby rewrites its title into one
# string padded with NULs, and ros_gz_sim starts it through /bin/sh -c.
RUBY_TITLE = ['gz sim -r -s -v2 /opt/worlds/turtlebot3_house.world'] + [''] * 20
SHELL_WRAPPER = ['/bin/sh', '-c', 'ruby /opt/gz_tools_vendor/bin/gz sim -r -s -v2 house.world']


@pytest.mark.parametrize('argv', [
    RUBY_TITLE,
    ['gz', 'sim', '-r', '-s', '-v2', 'house.world'],
    ['ruby', '/opt/ros/jazzy/opt/gz_tools_vendor/bin/gz', 'sim', '-s', 'house.world'],
    ['gz', 'sim', 'house.world'],   # server and GUI in one process
])
def test_a_simulation_server_is_recognised(argv):
    assert is_gazebo_server(argv)


@pytest.mark.parametrize('argv', [
    SHELL_WRAPPER,
    ['gz sim -g -v2 '],                                   # the GUI client alone
    ['gz', 'topic', '-l'],
    ['grep', 'gz sim'],
    ['/usr/bin/python3', '/opt/ros/jazzy/bin/ros2', 'launch', 'robot_bringup', 'demo.launch.py'],
    [],
])
def test_other_processes_are_not_servers(argv):
    assert not is_gazebo_server(argv)


def test_an_unset_partition_is_the_default_one():
    assert gazebo_partition({}) == ''
    assert gazebo_partition({'GZ_PARTITION': 'bench'}) == 'bench'


def fake_process(proc, pid, argv, environ=None):
    """Writes /proc/<pid>/cmdline and, unless None, environ the way the kernel lays them out."""
    folder = proc / str(pid)
    folder.mkdir()
    (folder / 'cmdline').write_bytes(b'\0'.join(arg.encode() for arg in argv) + b'\0')
    if environ is not None:
        (folder / 'environ').write_bytes(
            b''.join(f'{key}={value}'.encode() + b'\0' for key, value in environ.items()))


def test_servers_in_the_same_partition_are_found(tmp_path):
    fake_process(tmp_path, 4824, RUBY_TITLE, {'HOME': '/home/u'})
    fake_process(tmp_path, 4821, SHELL_WRAPPER, {'HOME': '/home/u'})
    fake_process(tmp_path, 900, ['gz', 'sim', '-s', 'w'], {'GZ_PARTITION': ''})
    assert running_gazebo_servers('', tmp_path) == [900, 4824]


def test_a_server_in_another_partition_does_not_count(tmp_path):
    """Two simulations on purpose, each with its own partition, do not see each other."""
    fake_process(tmp_path, 4824, RUBY_TITLE, {'GZ_PARTITION': 'bench'})
    assert running_gazebo_servers('', tmp_path) == []
    assert running_gazebo_servers('bench', tmp_path) == [4824]


def test_unreadable_and_vanished_processes_are_skipped(tmp_path):
    fake_process(tmp_path, 10, RUBY_TITLE)           # another user's: no environ to read
    (tmp_path / '11').mkdir()                        # exited during the scan
    (tmp_path / 'self').mkdir()                      # not a PID
    assert running_gazebo_servers('', tmp_path) == []


def test_the_error_names_every_server_and_how_to_stop_it():
    message = second_simulation_error([4821, 4824])
    assert 'PID 4821 4824' in message
    assert 'kill 4821 4824' in message
    assert 'GZ_PARTITION' in message
