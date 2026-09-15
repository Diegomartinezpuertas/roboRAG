"""Tests for browser driving: key set -> velocity, and the deadman (ADR-023).

The deadman is the part that matters. A person drives this robot from a browser
tab over HTTP; when that tab goes away mid-corridor, nothing sends a stop, and
only this logic prevents the last command from running forever.
"""

import pytest

from robot_dashboard.teleop import BOOST_FACTOR, STOP_REPEATS, TeleopState, twist_from_keys

LINEAR = 0.18
ANGULAR = 1.0


# --- keys to velocity ------------------------------------------------------

@pytest.mark.parametrize(('keys', 'expected'), [
    (['w'], (LINEAR, 0.0)),
    (['s'], (-LINEAR, 0.0)),
    (['a'], (0.0, ANGULAR)),
    (['d'], (0.0, -ANGULAR)),
    (['w', 'a'], (LINEAR, ANGULAR)),      # forward while turning
    ([], (0.0, 0.0)),
])
def test_held_keys_become_the_expected_velocity(keys, expected):
    assert twist_from_keys(keys, LINEAR, ANGULAR) == expected


@pytest.mark.parametrize('keys', [['w', 's'], ['a', 'd'], ['w', 's', 'a', 'd']])
def test_opposing_keys_cancel_instead_of_fighting(keys):
    """Rolling your fingers across the keys must never latch a direction."""
    linear, angular = twist_from_keys(keys, LINEAR, ANGULAR)
    assert (linear, angular) == (0.0, 0.0) or angular == 0.0


def test_keys_are_case_insensitive_and_unknown_keys_are_ignored():
    assert twist_from_keys(['W', 'ctrl', 'x'], LINEAR, ANGULAR) == (LINEAR, 0.0)


def test_boost_scales_both_axes_by_a_known_factor():
    linear, angular = twist_from_keys(['w', 'a'], LINEAR, ANGULAR, boost=True)
    assert linear == pytest.approx(LINEAR * BOOST_FACTOR)
    assert angular == pytest.approx(ANGULAR * BOOST_FACTOR)


def test_speeds_come_from_the_caller_so_a_client_cannot_ask_for_more():
    """The browser sends keys, never a velocity: the ceiling is the node's."""
    assert twist_from_keys(['w'], 0.05, 0.1) == (0.05, 0.0)


# --- the deadman -----------------------------------------------------------

def test_an_idle_dashboard_publishes_nothing_at_all():
    """Silence matters: /cmd_vel belongs to Nav2 whenever nobody is driving."""
    state = TeleopState(timeout_sec=0.5)
    assert state.tick(0.0) is None
    assert state.tick(100.0) is None


def test_a_fresh_command_is_published_as_given():
    state = TeleopState(timeout_sec=0.5)
    state.update(LINEAR, 0.0, now=10.0)
    assert state.tick(10.1) == (LINEAR, 0.0)
    assert state.is_active(10.1)


def test_a_command_that_is_never_refreshed_expires_into_a_stop():
    """The browser tab closed: no keyup, no refresh, and the robot must halt."""
    state = TeleopState(timeout_sec=0.5)
    state.update(LINEAR, ANGULAR, now=10.0)
    assert state.tick(10.4) == (LINEAR, ANGULAR)   # still fresh
    assert not state.is_active(10.6)
    assert state.tick(10.6) == (0.0, 0.0)          # stale -> stop


def test_the_stop_is_repeated_and_then_the_topic_is_released():
    state = TeleopState(timeout_sec=0.5)
    state.update(LINEAR, 0.0, now=0.0)
    stops = [state.tick(99.0) for _ in range(STOP_REPEATS)]
    assert stops == [(0.0, 0.0)] * STOP_REPEATS    # a dropped stop would be fatal
    assert state.tick(99.0) is None                # then silence again


def test_releasing_the_keys_sends_a_stop_without_waiting_for_the_timeout():
    state = TeleopState(timeout_sec=10.0)
    state.update(LINEAR, 0.0, now=0.0)
    state.update(0.0, 0.0, now=0.1)                # all keys released
    assert not state.is_active(0.1)
    assert state.tick(0.1) == (0.0, 0.0)


def test_an_explicit_stop_overrides_a_live_command():
    state = TeleopState(timeout_sec=10.0)
    state.update(LINEAR, ANGULAR, now=0.0)
    state.stop()
    assert state.tick(0.0) == (0.0, 0.0)
    assert not state.is_active(0.0)


def test_refreshing_keeps_the_same_command_alive_indefinitely():
    state = TeleopState(timeout_sec=0.5)
    for now in (0.0, 0.3, 0.6, 0.9):
        state.update(LINEAR, 0.0, now=now)
        assert state.tick(now + 0.1) == (LINEAR, 0.0)
