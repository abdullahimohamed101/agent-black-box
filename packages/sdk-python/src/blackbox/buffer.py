"""Bounded in-memory event buffer with priority classes (spec §67.3, §67.4).

Memory is bounded no matter what the backend does. When the buffer is full, lower priorities are
sacrificed first: an arriving event evicts the oldest queued event of strictly lower priority
(P2 before P1); if there is none it is itself dropped. P0 events (run lifecycle, errors, policy
blocks, approvals) are never dropped intentionally: they may exceed the bound up to `p0_overflow`
extra slots, and only beyond that hard cap are they dropped (and counted).
"""

import threading
from collections import deque
from typing import Any

from blackbox.stats import Stats

Event = dict[str, Any]


class EventBuffer:
    def __init__(self, max_queue: int, stats: Stats, p0_overflow: int | None = None) -> None:
        self.max_queue = max_queue
        self.hard_cap = max_queue + (max_queue if p0_overflow is None else p0_overflow)
        self._stats = stats
        self._lock = threading.Lock()
        self._queues: tuple[deque[Event], deque[Event], deque[Event]] = (deque(), deque(), deque())
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def put(self, event: Event, priority: int) -> bool:
        """Queue an event; False if it (or nothing) was dropped to make room."""
        with self._lock:
            if self._size >= self.max_queue and not self._make_room(priority):
                self._stats.add(f"dropped_p{priority}")
                return False
            self._queues[priority].append(event)
            self._size += 1
            return True

    def _make_room(self, priority: int) -> bool:
        for victim in (2, 1):
            if victim > priority and self._queues[victim]:
                self._queues[victim].popleft()
                self._size -= 1
                self._stats.add(f"dropped_p{victim}")
                return True
        return priority == 0 and self._size < self.hard_cap

    def take(self, limit: int) -> list[Event]:
        """Remove up to `limit` events, most important first, oldest first within a class."""
        out: list[Event] = []
        with self._lock:
            for queue in self._queues:
                while queue and len(out) < limit:
                    out.append(queue.popleft())
            self._size -= len(out)
        out.sort(key=lambda e: e.get("event_id", ""))  # ULIDs: creation order within the batch
        return out

    def clear(self) -> int:
        with self._lock:
            n, self._size = self._size, 0
            for queue in self._queues:
                queue.clear()
            return n
