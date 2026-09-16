"""Tests for arbitrating /cmd_vel between manual driving and Nav2 (ADR-029)."""

import pytest

from robot_skills.cmd_vel_mux import CmdVelMux, MuxSource


@pytest.fixture
def mux():
    return CmdVelMux([
        MuxSource('teleop', priority=2, timeout_sec=0.5),
        MuxSource('nav2', priority=1, timeout_sec=0.5),
    ])


def test_nothing_is_active_before_anyone_speaks(mux):
    assert mux.active(0.0) is None


def test_nav2_drives_when_nobody_else_does(mux):
    assert mux.offer('nav2', 1.0) is True
    assert mux.active(1.1) == 'nav2'


def test_manual_driving_takes_over_mid_goal(mux):
    """The reason the mux exists: pressing a key must win over Nav2 at once."""
    mux.offer('nav2', 1.0)
    assert mux.offer('teleop', 1.05) is True
    assert mux.offer('nav2', 1.10) is False          # Nav2's commands are dropped
    assert mux.active(1.10) == 'teleop'


def test_letting_go_hands_control_back_to_nav2(mux):
    mux.offer('teleop', 1.0)
    mux.offer('nav2', 1.2)
    assert mux.active(1.2) == 'teleop'
    assert mux.offer('nav2', 1.6) is True             # teleop silent for 0.6 s > 0.5 s
    assert mux.active(1.6) == 'nav2'


def test_a_lower_priority_source_cannot_steal_control_while_the_higher_is_fresh(mux):
    mux.offer('teleop', 1.0)
    for step in range(5):
        assert mux.offer('nav2', 1.0 + 0.09 * step) is False


def test_everything_silent_again_means_idle(mux):
    mux.offer('nav2', 1.0)
    assert mux.active(2.0) is None


def test_unknown_sources_and_duplicate_names_are_rejected(mux):
    with pytest.raises(KeyError):
        mux.offer('joystick', 1.0)
    with pytest.raises(ValueError):
        CmdVelMux([MuxSource('a', 1, 0.5), MuxSource('a', 2, 0.5)])
