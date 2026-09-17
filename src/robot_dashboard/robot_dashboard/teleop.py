"""Keyboard driving translated into velocity commands — pure logic, no ROS imports.

The dashboard lets a person drive the robot with WASD before any autonomy is
involved, so SLAM Toolbox can be walked through the house and produce a map the
agent can then work on (see docs/decisions/ADR-023-browser-teleop.md).

Two things make that safe to do from a browser, and both live here so they can
be unit-tested without a robot:

* **Key set to twist.** The browser sends which keys are held, not a velocity;
  the speeds come from the node's parameters, and every command is capped at
  the TurtleBot3 Waffle's documented maxima, so neither a hostile client nor a
  mistyped parameter can drive faster than the real base could.
* **A deadman.** Held keys are a stream of refreshes, never a latch. If the
  browser tab closes, the laptop sleeps, or the network drops, no refresh
  arrives, the command goes stale within `timeout_sec`, and the robot is sent a
  zero velocity instead of continuing into a wall.

`dashboard_node` owns the publisher and the timer; everything it decides is
decided here.
"""

from __future__ import annotations

# Held keys are normalized to these before the twist is computed; the browser
# maps its arrow keys onto them so both layouts behave identically.
FORWARD_KEYS = frozenset({'w'})
BACKWARD_KEYS = frozenset({'s'})
LEFT_KEYS = frozenset({'a'})
RIGHT_KEYS = frozenset({'d'})

# The TurtleBot3 Waffle's documented maxima. Every command is clamped to them:
# the simulated base would happily spin faster, and driving a robot faster than
# it can scan is what corrupts a SLAM map (measured 2026-09-17 — a slip while
# pushing against furniture cost two maps). Above these speeds the simulation
# would also stop being a simulation of this robot.
MAX_LINEAR_SPEED = 0.26
MAX_ANGULAR_SPEED = 1.82

# Holding shift multiplies both speeds, up to the maxima above. With the node's
# defaults now at those maxima it changes nothing; it is a boost for anyone who
# configures slower speeds for precision driving.
BOOST_FACTOR = 1.4

# Zero-velocity messages sent when a driving session ends. One would be enough
# on a reliable transport; /cmd_vel is best-effort through a Gazebo bridge, and
# a dropped stop message is the one message that must not be dropped.
STOP_REPEATS = 3


def twist_from_keys(
    keys: set[str] | frozenset[str] | list[str],
    linear_speed: float,
    angular_speed: float,
    boost: bool = False,
) -> tuple[float, float]:
    """Converts the set of currently held keys into a (linear, angular) velocity.

    Opposing keys cancel (W+S is a stop, A+D drives straight), which is what a
    person expects when rolling their fingers across the keys, and it also
    means a stuck key plus its opposite never latches a turn.

    Both axes are clamped to MAX_LINEAR_SPEED / MAX_ANGULAR_SPEED, whatever the
    parameters and the boost ask for.

    Args:
        keys: Keys currently held, e.g. {"w", "a"}. Case and unknown keys are
            tolerated: anything not in the WASD set is ignored.
        linear_speed: Forward/backward speed in m/s for a fully held key.
        angular_speed: Turning speed in rad/s for a fully held key.
        boost: True while shift is held, scaling both speeds by BOOST_FACTOR.

    Returns:
        Tuple of (linear m/s, angular rad/s); (0.0, 0.0) when nothing is held.
    """
    held = {key.lower() for key in keys}
    linear = float(bool(held & FORWARD_KEYS)) - float(bool(held & BACKWARD_KEYS))
    angular = float(bool(held & LEFT_KEYS)) - float(bool(held & RIGHT_KEYS))
    scale = BOOST_FACTOR if boost else 1.0
    return (
        _capped(linear * linear_speed * scale, MAX_LINEAR_SPEED),
        _capped(angular * angular_speed * scale, MAX_ANGULAR_SPEED),
    )


def _capped(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


class TeleopState:
    """The current manual-driving command, and whether it is still alive.

    Every method takes `now` (a monotonic clock reading) rather than reading the
    clock itself, so the staleness logic is testable without sleeping.

    Args:
        timeout_sec: How long a command stays valid without a refresh from the
            browser. Must comfortably exceed the UI's refresh interval and stay
            short enough that an abandoned session stops the robot within a few
            centimeters of travel.
    """

    def __init__(self, timeout_sec: float = 0.6) -> None:
        self._timeout_sec = timeout_sec
        self._linear = 0.0
        self._angular = 0.0
        self._stamp = 0.0
        self._stops_pending = 0

    def update(self, linear: float, angular: float, now: float) -> None:
        """Records the command the browser is asking for, refreshing the deadman.

        Args:
            linear: Requested linear velocity in m/s.
            angular: Requested angular velocity in rad/s.
            now: Current monotonic time in seconds.
        """
        if linear == 0.0 and angular == 0.0:
            self.stop()
            return
        self._linear = linear
        self._angular = angular
        self._stamp = now
        self._stops_pending = STOP_REPEATS

    def stop(self) -> None:
        """Drops the current command, leaving the queued stop messages to `tick`.

        The queue was filled by the last live `update`, so a dashboard that has
        never driven the robot still publishes nothing at all.
        """
        self._linear = 0.0
        self._angular = 0.0
        self._stamp = 0.0

    def is_active(self, now: float) -> bool:
        """True while a non-zero command is still fresh enough to be obeyed."""
        if self._linear == 0.0 and self._angular == 0.0:
            return False
        return (now - self._stamp) <= self._timeout_sec

    def tick(self, now: float) -> tuple[float, float] | None:
        """Returns the velocity to publish right now, or None to publish nothing.

        Silence is the normal state: while nobody is driving, the dashboard must
        not publish at all, or it would fight Nav2 for /cmd_vel on every
        autonomous goal. It returns zeros only to end a driving session — the
        command expired, or the user released the keys — and only STOP_REPEATS
        times, after which it falls silent again.

        Args:
            now: Current monotonic time in seconds.

        Returns:
            (linear, angular) to publish, or None if nothing should be sent.
        """
        if self.is_active(now):
            return (self._linear, self._angular)
        # Expired or released: make sure the robot actually hears about it.
        self.stop()
        if self._stops_pending > 0:
            self._stops_pending -= 1
            return (0.0, 0.0)
        return None
