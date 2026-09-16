"""Who drives the robot right now — priority arbitration of velocity commands, no ROS imports.

Before this, the dashboard's WASD driving (ADR-023) and Nav2 both published
straight to /cmd_vel, and the robot obeyed whichever message arrived last: two
controllers fighting at 20 Hz each. The only safe way to drive by hand was to
launch without Nav2.

The mux puts one owner on /cmd_vel. Each source publishes to its own topic;
the source with the highest priority that has sent a command recently holds
control, and everything from lower-priority sources is dropped while it does.
Manual driving outranks Nav2, so a person can take over mid-goal just by
pressing a key, and hands control back simply by letting go: once manual
commands stop for `timeout_sec`, Nav2's commands pass again.

`twist_mux` does this in the ROS ecosystem. It is not a dependency of this
workspace — adding it means a system package on every machine, in the container
image and in CI — and the arbitration is a few lines of logic that deserve their
own tests anyway. See docs/decisions/ADR-029-cmd-vel-mux.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MuxSource:
    """One source of velocity commands.

    Attributes:
        name: Label reported as the active source, e.g. "teleop".
        priority: Higher wins while it is active.
        timeout_sec: How long after its last message a source keeps control.
    """

    name: str
    priority: int
    timeout_sec: float


class CmdVelMux:
    """Decides which source's command reaches the robot.

    Every method takes `now` (a monotonic clock reading) so arbitration is
    testable without sleeping.

    Args:
        sources: The competing sources; names must be unique.
    """

    def __init__(self, sources: list[MuxSource]) -> None:
        names = [s.name for s in sources]
        if len(set(names)) != len(names):
            raise ValueError(f'Duplicate mux source names: {names}')
        self._sources = {s.name: s for s in sources}
        self._last_seen: dict[str, float] = {}

    def offer(self, name: str, now: float) -> bool:
        """Records a command from a source and says whether to forward it.

        Args:
            name: The source that produced the command.
            now: Current monotonic time in seconds.

        Returns:
            True if this source holds control now, so its command is published.

        Raises:
            KeyError: If the source is unknown.
        """
        if name not in self._sources:
            raise KeyError(f'Unknown mux source: {name}')
        self._last_seen[name] = now
        return self.active(now) == name

    def active(self, now: float) -> str | None:
        """Returns the source holding control, or None when every source is silent."""
        fresh = [
            source for name, source in self._sources.items()
            if name in self._last_seen
            and now - self._last_seen[name] <= source.timeout_sec
        ]
        if not fresh:
            return None
        return max(fresh, key=lambda s: s.priority).name
