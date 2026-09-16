"""How fast the simulation runs compared to the wall clock — pure logic, no ROS imports.

"The robot doesn't move" was reported during a manual mapping run. The command
path turned out fine; what a person cannot see from the dashboard is whether
the simulator is crawling. On WSL2 Gazebo renders in software and the real-time
factor (RTF) drifts with load: 0.68 headless, 0.59 with the GUI open, measured
on this machine — and every speed the robot is given is scaled by it. The
dashboard shows the RTF next to the drive controls, estimated from /clock.

See docs/decisions/ADR-030-dashboard-redesign-and-browser-tests.md.
"""

from __future__ import annotations

from collections import deque


class RtfEstimator:
    """Estimates the real-time factor over a sliding window of /clock samples.

    Args:
        window_sec: Wall-clock span the estimate averages over. Long enough to
            smooth /clock's bursty publishing, short enough to show a slowdown
            within a few seconds.
    """

    def __init__(self, window_sec: float = 5.0) -> None:
        self._window_sec = window_sec
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, sim_sec: float, wall_sec: float) -> None:
        """Records one (simulation time, wall time) sample, both in seconds.

        A simulation clock that jumps backwards (the simulator was restarted)
        discards the history instead of producing a negative factor.
        """
        if self._samples and sim_sec < self._samples[-1][0]:
            self._samples.clear()
        self._samples.append((sim_sec, wall_sec))
        while self._samples and wall_sec - self._samples[0][1] > self._window_sec:
            self._samples.popleft()

    def rtf(self, wall_now: float, stale_after_sec: float = 3.0) -> float | None:
        """Returns simulated seconds per wall second, or None when unknown.

        Args:
            wall_now: Current wall time in seconds, to detect a silent /clock.
            stale_after_sec: With no sample this recent, the simulator is
                paused or gone, and a number would be a lie.

        Returns:
            The factor (1.0 = real time), or None without enough fresh data.
        """
        if len(self._samples) < 2 or wall_now - self._samples[-1][1] > stale_after_sec:
            return None
        (sim0, wall0), (sim1, wall1) = self._samples[0], self._samples[-1]
        if wall1 - wall0 < 0.5:
            return None
        return max(0.0, (sim1 - sim0) / (wall1 - wall0))
