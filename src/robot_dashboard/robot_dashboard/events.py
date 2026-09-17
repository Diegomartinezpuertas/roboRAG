"""The dashboard's event buffer — pure logic, no ROS imports.

The page polls `/api/events?since=<id>` and keeps the cursor in the browser, so
this buffer decides two things a live dashboard depends on: that a page left
open across a relaunch of the node recovers instead of freezing (its cursor
belongs to a dead process), and that clearing the thread before a recording
does not make earlier pages replay it (ADR-030).
"""

from __future__ import annotations

import threading
import time


class EventBuffer:
    """Thread-safe ring buffer of dashboard events with monotonically increasing IDs.

    Args:
        max_events: Maximum number of events retained.
    """

    def __init__(self, max_events: int = 3000) -> None:
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._next_id = 1
        self._max_events = max_events

    def append(self, event_type: str, text: str, source: str = '', level: int = 20) -> None:
        """Appends an event, evicting the oldest entries beyond capacity.

        Args:
            event_type: One of "goal", "status", "response", "rosout".
            text: Event message text.
            source: Originating node name (for rosout events).
            level: rcl log severity (10=DEBUG ... 50=FATAL).
        """
        with self._lock:
            self._events.append({
                'id': self._next_id, 'ts': time.time(), 'type': event_type,
                'text': text, 'source': source, 'level': level,
            })
            self._next_id += 1
            if len(self._events) > self._max_events:
                del self._events[: len(self._events) - self._max_events]

    def clear(self) -> int:
        """Drops every event held, so a new recording starts on an empty thread.

        Ids keep increasing, so a page that has already polled is not sent the
        same events again.

        Returns:
            How many events were dropped.
        """
        with self._lock:
            dropped = len(self._events)
            self._events.clear()
        return dropped

    def since(self, last_id: int) -> list[dict]:
        """Returns the events a client has not seen yet.

        A page left open across a restart of this node asks for ids past
        anything this buffer will ever hold — its cursor belongs to the
        previous process — and used to receive nothing for ever, so the
        timeline stayed frozen until someone reloaded the page. Such a cursor
        is treated as a fresh start instead.

        Args:
            last_id: The highest event id the client already has; 0 for a new page.

        Returns:
            The events with a greater id, or all of them when the cursor is
            ahead of this buffer.

        """
        with self._lock:
            if last_id >= self._next_id:
                return list(self._events)
            return [e for e in self._events if e['id'] > last_id]
