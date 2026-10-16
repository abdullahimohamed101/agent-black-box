"""Shared helpers of scripts/obs-e2e.sh (Phase 19c step 6). Built on hardening_e2e_lib (processes, clients, the flood tolerance helper,
the load generator); this module adds scraping and parsing of /metrics, exact-delta arithmetic, bounded polling, a TCP proxy that can
stop and resume (a real database outage), the sentinel register for the log scan and the worker process. Run in the API's
environment (`apps/api/.venv`). Secrets are never printed: failures name the check, not a value.

Rules every gate follows (the lesson of the 19a CI failures):
  * assert deterministic facts only: exact counter deltas, label sets, status codes, presence and absence;
  * never a wall-clock or absolute latency threshold; timings are printed as information only;
  * an "eventually" fact (Prometheus has scraped, a rule has evaluated, a span was exported) is a bounded poll with a generous
    deadline that fails with a message naming what never happened;
  * floods use `only_statuses` / `transport_budget` (a same-machine flood can lose a connection at the accept queue).
"""

import asyncio
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent))

import hardening_e2e_lib as base  # noqa: E402
from hardening_e2e_lib import (  # noqa: E402, F401
    Proc,
    Reply,
    batch_of,
    bearer,
    event,
    fire,
    new_key,
    new_project,
    only_statuses,
    person,
    run_concurrent,
    run_ids,
    tally,
    transport_budget,
)

ROOT = base.ROOT
WORK = base.WORK
API_DIR = base.API_DIR
API_PORT = int(os.environ.get("OBS_E2E_API_PORT", "8240"))
WORKER_METRICS_PORT = int(os.environ.get("OBS_E2E_WORKER_METRICS_PORT", str(API_PORT + 9)))
API_URL = f"http://127.0.0.1:{API_PORT}"
WORKER_URL = f"http://127.0.0.1:{WORKER_METRICS_PORT}"
TOKEN_FILE = Path(os.environ.get("E2E_METRICS_TOKEN_FILE", "/dev/null"))
OTEL_PYTHON = os.environ.get("E2E_OTEL_PYTHON", "")
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# ------------------------------------------------------------------------------------------------ results (the base table, our text)

gate = base.gate
check = base.check
info = base.info


def die(message: str) -> None:
    print(f"OBS-E2E CHECK FAILED [{base._gate}]: {message}", file=sys.stderr)
    raise SystemExit(1)


def finish() -> None:
    if base._failures:
        print(f"OBS-E2E CHECK FAILED [{base._gate}]: {len(base._failures)} failed: {base._failures}", file=sys.stderr)
        raise SystemExit(1)


base.die = die  # the shared helpers (start_api, cli, ...) fail with this module's wording
base.finish = finish


def metrics_token() -> str:
    return TOKEN_FILE.read_text().strip()


# ------------------------------------------------------------------------------------------------ the exposition


def _load_parser() -> Any:
    """The minimal exposition parser of tests/obs (loaded by path: its name would shadow a stdlib module)."""
    spec = importlib.util.spec_from_file_location("abb_obs_parser", API_DIR / "tests" / "obs" / "parser.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_parser = _load_parser()
Family = _parser.Family


def parse(text: str) -> dict[str, Any]:
    return dict(_parser.parse(text))


def scrape_raw(url: str = API_URL, token: str | None = None, *, path: str = "/metrics", **kwargs: Any) -> httpx.Response:
    headers = {"Authorization": f"Bearer {metrics_token() if token is None else token}"} if token != "" else {}
    return httpx.get(f"{url}{path}", headers=headers, timeout=15, **kwargs)


def note_moved(families: dict[str, Any]) -> None:
    """Remember which catalogue families showed a non-zero sample in any exposition of the run (the coverage table of the summary).
    Each stage is its own process, so the set lives in a file."""
    path = WORK / "moved.json"
    try:
        moved = set(json.loads(path.read_text())) if path.exists() else set()
    except ValueError:
        moved = set()
    for name, family in families.items():
        if any(value == value and value != 0 for _, _, value in family.samples):  # NaN is no movement
            moved.add(name)
    path.write_text(json.dumps(sorted(moved)))


def scrape(url: str = API_URL) -> dict[str, Any]:
    """The parsed exposition of an API or worker. Fails the gate if it is not a clean 200."""
    response = scrape_raw(url)
    if response.status_code != 200:
        die(f"scrape {url} -> {response.status_code}")
    try:
        families = parse(response.text)
    except AssertionError as error:
        die(f"the exposition of {url} does not parse: {error}")
        raise
    note_moved(families)
    return families


def value(families: dict[str, Any], family: str, sample: str | None = None, **labels: str) -> float:
    """One sample's value; 0.0 for a series that has not been created yet (a counter starts at its first increment)."""
    fam = families.get(family)
    if fam is None:
        die(f"family {family} is not in the exposition")
    name = sample or family
    for sample_name, got, number in fam.samples:
        if sample_name == name and got == labels:
            return float(number)
    return 0.0


def total(families: dict[str, Any], family: str, sample: str | None = None, **labels: str) -> float:
    """The sum of every sample of `family` whose labels include `labels` (a subset match)."""
    fam = families.get(family)
    if fam is None:
        die(f"family {family} is not in the exposition")
    name = sample or family
    return float(sum(v for n, got, v in fam.samples if n == name and all(got.get(k) == want for k, want in labels.items())))


def series_count(families: dict[str, Any]) -> int:
    return sum(len(f.samples) for f in families.values())


class Delta:
    """Before/after scrapes of one process: `d(...)` is the exact change of a series (or of a label subset's sum)."""

    def __init__(self, url: str = API_URL) -> None:
        self.url = url
        self.before = scrape(url)

    def after(self) -> dict[str, Any]:
        self.now = scrape(self.url)
        return self.now

    def d(self, family: str, sample: str | None = None, **labels: str) -> float:
        now = getattr(self, "now", None) or self.after()
        return total(now, family, sample, **labels) - total(self.before, family, sample, **labels)

    def refresh(self) -> "Delta":
        self.now = scrape(self.url)
        return self


def poll(what: str, probe: Callable[[], Any], *, timeout: float = 120.0, every: float = 0.5) -> Any:
    """Bounded polling for an eventual fact: returns the first truthy probe result, or fails naming `what`."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            result = probe()
        except (httpx.HTTPError, OSError, AssertionError, KeyError, ValueError, IndexError) as error:
            last = f"{type(error).__name__}"
        else:
            if result:
                return result
            last = result
        time.sleep(every)
    die(f"never happened within {timeout:.0f} s: {what} (last observation: {last!r})")
    raise AssertionError  # unreachable: die exits


# ------------------------------------------------------------------------------------------------ sentinels and secrets


def sentinel(kind: str) -> str:
    """A unique, recognisable marker planted in client-controlled places; the log scan (gate 4) fails if one appears in any log."""
    mark = f"SENTINEL-{kind}-{os.environ.get('E2E_SUFFIX', '0')}-{os.urandom(3).hex()}"
    current = base.state().get("sentinels", {})
    current[kind] = mark
    base.save(sentinels=current)
    return mark


def remember_secret(label: str, secret: str) -> None:
    """Register a secret the log scan must never find (an API key, a session cookie, a token). Stored in the 0600 state file only."""
    current = base.state().get("secrets", {})
    current[label] = secret
    base.save(secrets=current)


# ------------------------------------------------------------------------------------------------ processes


def start_worker(name: str, *, python: str | None = None, env: dict[str, str | None] | None = None, metrics_port: int | None = None,
                 log_prefix: str = "worker") -> Proc:
    merged = base._base_env()
    for key, val in (env or {}).items():
        if val is None:
            merged.pop(key, None)
        else:
            merged[key] = val
    if metrics_port is not None:
        merged["ABB_WORKER_METRICS_PORT"] = str(metrics_port)
    log = WORK / f"{log_prefix}-{name}.log"
    popen = subprocess.Popen(  # noqa: S603
        [python or str(base.API_PYTHON), "-m", "abb_api.worker"], cwd=API_DIR, env=merged, stdout=log.open("wb"), stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    proc = Proc(name, popen, log, metrics_port or 0, {})
    base._started.append(proc)
    return proc


def start_server(name: str, port: int, env: dict[str, str | None] | None = None, *, python: str | None = None, wait: bool = True,
                 log_prefix: str = "api", owner_role: bool = False, host: str = "127.0.0.1") -> Proc:
    """An API process, optionally from another interpreter (the otel environment) or on another interface (`0.0.0.0` only where a
    container must reach it). Same contract as base.start_api: `env` overrides settings, None unsets one."""
    merged = base._base_env()
    if owner_role:
        merged["ABB_DATABASE_URL"] = os.environ["E2E_OWNER_DB_URL"]
    for key, val in (env or {}).items():
        if val is None:
            merged.pop(key, None)
        else:
            merged[key] = val
    log = WORK / f"{log_prefix}-{name}.log"
    popen = subprocess.Popen(  # noqa: S603
        [python or str(base.API_PYTHON), "-m", "uvicorn", "abb_api.main:app_from_env", "--factory", "--port", str(port), "--host", host],
        cwd=API_DIR, env=merged, stdout=log.open("wb"), stderr=subprocess.STDOUT, start_new_session=True,
    )
    proc = Proc(name, popen, log, port, {})
    base._started.append(proc)
    if wait and not base.wait_ready(proc):
        die(f"API {name} did not become ready (exit={popen.poll()}): {log.read_text(errors='replace').splitlines()[-6:]}")
    return proc


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def closed_port() -> int:
    """A loopback port nothing listens on (bound and released a moment ago)."""
    return free_port()


# ------------------------------------------------------------------------------------------------ a TCP proxy that can go down


class TcpProxy:
    """Forwards 127.0.0.1:<port> to the real database. `stop()` closes the listener and every open connection (the database
    "goes down"); `start()` listens again on the same port (it "comes back"). The processes under test see a real outage."""

    def __init__(self, target_host: str, target_port: int) -> None:
        self.target = (target_host, target_port)
        self.port = free_port()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self.accepted = 0
        self._thread.start()

    def _run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=20)

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError, OSError):
            pass
        finally:
            writer.close()

    async def _handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        self.accepted += 1
        try:
            up_r, up_w = await asyncio.open_connection(*self.target)
        except OSError:
            client_w.close()
            return
        self._writers |= {client_w, up_w}
        await asyncio.gather(self._pipe(client_r, up_w), self._pipe(up_r, client_w))
        self._writers -= {client_w, up_w}

    async def _start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port, reuse_address=True)

    async def _stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()  # stop listening first: new connections are refused from here on
        for writer in list(self._writers):
            writer.close()  # then cut the open ones
        self._writers.clear()
        if server is not None:
            try:
                await asyncio.wait_for(server.wait_closed(), 5)
            except TimeoutError:
                pass

    def start(self) -> None:
        self._run(self._start())

    def stop(self) -> None:
        self._run(self._stop())

    def close(self) -> None:
        self.stop()
        self._loop.call_soon_threadsafe(self._loop.stop)


def proxied_url(proxy: TcpProxy) -> str:
    """The runtime URL of the stack, pointed at `proxy`."""
    from sqlalchemy.engine import make_url  # noqa: PLC0415

    return make_url(os.environ["E2E_RUNTIME_DB_URL"]).set(host="127.0.0.1", port=proxy.port).render_as_string(hide_password=False)


def db_target() -> tuple[str, int]:
    from sqlalchemy.engine import make_url  # noqa: PLC0415

    url = make_url(os.environ["E2E_RUNTIME_DB_URL"])
    return url.host or "127.0.0.1", int(url.port or 5432)


# ------------------------------------------------------------------------------------------------ small helpers


def ingest(base_url: str, token: str, events: list[dict[str, Any]], *, extra_headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.post(f"{base_url}/v1/events/batch", json=batch_of(events), headers={**bearer(token), **(extra_headers or {})}, timeout=30)


def docker(*args: str, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False, timeout=timeout)  # noqa: S603, S607


def timing(**evidence: Any) -> None:
    """Timings are information only; they are never asserted."""
    print("  timing (informational, never asserted): " + json.dumps(evidence, sort_keys=True, default=str), flush=True)
