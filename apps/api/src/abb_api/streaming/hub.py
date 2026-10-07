"""In-process fan-out of Postgres NOTIFY wake-ups to live streams (ADR-022, D2).

One dedicated LISTEN connection per API process. A notification only says "something arrived
for this run"; subscribers then query. Notifications are a latency optimisation, never the
source of truth: every wait has a timeout so streams also poll, and every (re)connect wakes
everyone because notifications sent while the listener was down are gone.
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterator

import asyncpg
from sqlalchemy.engine import make_url

from abb_api.streaming import notify
from abb_api.streaming.notify import StreamKey

log = logging.getLogger("abb.streaming")

APPLICATION_NAME = "abb-stream-listener"
CONNECT_TIMEOUT_SECONDS = 3.0
PING_TIMEOUT_SECONDS = 3.0


class Subscription:
    """One stream's interest in one run. Wake-ups coalesce: many notifications make one wake."""

    def __init__(self, hub: "StreamHub", key: StreamKey) -> None:
        self._hub = hub
        self._key = key
        self._wake = asyncio.Event()

    def wake(self) -> None:
        self._wake.set()

    async def wait(self, seconds: float) -> bool:
        """True when woken by a notification, False after `seconds` (time for a fallback poll).

        The flag is cleared before returning, so a notification that lands while the caller queries
        makes the next wait return at once instead of being lost.
        """
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
            return True
        except TimeoutError:
            return False
        finally:
            self._wake.clear()

    def close(self) -> None:
        self._hub.unsubscribe(self._key, self)


class StreamHub:
    def __init__(
        self,
        database_url: str,
        *,
        reconnect_min_seconds: float = 0.2,
        reconnect_max_seconds: float = 5.0,
        heartbeat_seconds: float = 5.0,
    ) -> None:
        # asyncpg wants a plain postgresql:// DSN, not SQLAlchemy's driver-qualified URL.
        self._dsn = make_url(database_url).set(drivername="postgresql").render_as_string(False)
        self._reconnect_min = reconnect_min_seconds
        self._reconnect_max = reconnect_max_seconds
        self._heartbeat = heartbeat_seconds
        self._subscribers: dict[StreamKey, set[Subscription]] = {}
        self._task: asyncio.Task[None] | None = None
        self._connected = False
        self._connected_event = asyncio.Event()

    # ------------------------------------------------------------------ subscribers

    def subscribe(self, key: StreamKey) -> Subscription:
        subscription = Subscription(self, key)
        self._subscribers.setdefault(key, set()).add(subscription)
        return subscription

    def unsubscribe(self, key: StreamKey, subscription: Subscription) -> None:
        group = self._subscribers.get(key)
        if group is None:
            return
        group.discard(subscription)
        if not group:
            del self._subscribers[key]

    @property
    def subscriber_count(self) -> int:
        return sum(len(group) for group in self._subscribers.values())

    def _wake(self, key: StreamKey) -> None:
        for subscription in tuple(self._subscribers.get(key, ())):
            subscription.wake()

    def _wake_all(self) -> None:
        for group in tuple(self._subscribers.values()):
            for subscription in tuple(group):
                subscription.wake()

    # ------------------------------------------------------------------ listener lifecycle

    @property
    def connected(self) -> bool:
        return self._connected

    async def wait_connected(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._connected_event.wait(), seconds)
            return True
        except TimeoutError:
            return False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="abb-stream-hub")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _on_notify(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        key = notify.decode(payload)
        if key is not None:  # anything else is noise on a shared channel
            self._wake(key)

    async def _run(self) -> None:
        delay = self._reconnect_min
        while True:
            conn: asyncpg.Connection | None = None
            try:
                conn = await asyncpg.connect(
                    self._dsn,
                    timeout=CONNECT_TIMEOUT_SECONDS,
                    server_settings={"application_name": APPLICATION_NAME},
                )
                lost = asyncio.Event()
                conn.add_termination_listener(lambda _c, gone=lost: gone.set())
                await conn.add_listener(notify.CHANNEL, self._on_notify)
                self._connected = True
                self._connected_event.set()
                delay = self._reconnect_min
                log.info("stream listener connected")
                self._wake_all()  # notifications sent while we were away are gone: re-read
                while not lost.is_set():
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(lost.wait(), self._heartbeat)
                    if not lost.is_set():
                        await asyncio.wait_for(conn.fetchval("SELECT 1"), PING_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                raise
            except (
                Exception
            ) as exc:  # any failure means "reconnect"; streams keep polling meanwhile
                # Class name only: exception text can carry connection details.
                log.warning("stream listener unavailable (%s); retrying", type(exc).__name__)
            finally:
                self._connected = False
                self._connected_event.clear()
                if conn is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(conn.close(), 2.0)
            await asyncio.sleep(delay)
            delay = min(self._reconnect_max, delay * 2)


@contextlib.contextmanager
def subscription(hub: StreamHub, key: StreamKey) -> Iterator[Subscription]:
    sub = hub.subscribe(key)
    try:
        yield sub
    finally:
        sub.close()
