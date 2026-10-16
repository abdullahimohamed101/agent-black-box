"""Every call that can wait on the network or a child process has an explicit bound (ADR-169).

A parse of the source (`ast`, no imports executed) fails when a call from the table below has no
explicit timeout argument and is not lexically inside `asyncio.timeout(...)` or an argument of
`asyncio.wait_for(...)`. A call that is bounded some other way goes in `ALLOWED` with the reason
and the mechanism, reviewed like code: an entry that no longer matches anything fails too, so the
list cannot rot. Client *methods* (`client.post(...)`) are bounded by the timeout their client was
built with, which is why the constructors are the checked calls.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

ROOTS = (
    Path(__file__).resolve().parents[1] / "src" / "abb_api",
    Path(__file__).resolve().parents[3] / "packages" / "sdk-python" / "src",
)

# dotted name -> how a timeout is spelled: (keyword, positional index or None)
TIMEOUT_ARGUMENT: dict[str, tuple[str, int | None]] = {
    "httpx.AsyncClient": ("timeout", None),
    "httpx.Client": ("timeout", None),
    "httpx.get": ("timeout", None),
    "httpx.post": ("timeout", None),
    "httpx.put": ("timeout", None),
    "httpx.patch": ("timeout", None),
    "httpx.delete": ("timeout", None),
    "httpx.head": ("timeout", None),
    "httpx.request": ("timeout", None),
    "httpx.stream": ("timeout", None),
    "urllib.request.urlopen": ("timeout", 2),
    "socket.create_connection": ("timeout", 1),
    "smtplib.SMTP": ("timeout", 3),
    "smtplib.SMTP_SSL": ("timeout", 3),
    "http.client.HTTPConnection": ("timeout", 3),
    "http.client.HTTPSConnection": ("timeout", 3),
    "ftplib.FTP": ("timeout", 3),
    "imaplib.IMAP4": ("timeout", None),
    "poplib.POP3": ("timeout", None),
    "asyncpg.connect": ("timeout", None),
    "asyncpg.create_pool": ("timeout", None),
    "subprocess.run": ("timeout", None),
    "subprocess.call": ("timeout", None),
    "subprocess.check_call": ("timeout", None),
    "subprocess.check_output": ("timeout", None),
}
# Calls with no timeout parameter at all: they must sit inside an explicit timeout context.
NEEDS_CONTEXT = {
    "asyncio.open_connection",
    "asyncio.open_unix_connection",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "subprocess.Popen",
    "socket.socket",
}
# Any use of these modules' request functions is a violation unless they carry a timeout.
REQUESTS = {f"requests.{m}" for m in ("get", "post", "put", "patch", "delete", "head", "request")}
TIMEOUT_CONTEXTS = {"asyncio.timeout", "asyncio.timeout_at", "asyncio.wait_for", "anyio.fail_after"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    call: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.path, self.call)


def _aliases(tree: ast.Module) -> dict[str, str]:
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _dotted(node: ast.expr, aliases: dict[str, str]) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    root = aliases.get(node.id)
    if root is None:
        return None
    return ".".join([root, *reversed(parts)])


class _Scan(ast.NodeVisitor):
    def __init__(self, path: str, aliases: dict[str, str]) -> None:
        self.path = path
        self.aliases = aliases
        self.findings: list[Finding] = []
        self._bounded = 0  # depth of enclosing timeout contexts

    def visit_With(self, node: ast.With) -> None:
        self._with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._with(node)

    def _with(self, node: ast.With | ast.AsyncWith) -> None:
        bounded = any(
            isinstance(item.context_expr, ast.Call)
            and _dotted(item.context_expr.func, self.aliases) in TIMEOUT_CONTEXTS
            for item in node.items
        )
        for item in node.items:
            self.visit(item.context_expr)
        self._bounded += bounded
        for child in node.body:
            self.visit(child)
        self._bounded -= bounded

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted(node.func, self.aliases)
        if name in TIMEOUT_CONTEXTS and name.endswith("wait_for"):
            # Everything passed to wait_for is bounded by it (the first argument is the awaitable).
            self._bounded += 1
            for child in ast.iter_child_nodes(node):
                self.visit(child)
            self._bounded -= 1
            return
        if name is not None and not self._bounded and not self._has_timeout(name, node):
            self.findings.append(Finding(self.path, node.lineno, name))
        self.generic_visit(node)

    def _has_timeout(self, name: str, node: ast.Call) -> bool:
        if name in NEEDS_CONTEXT:
            return False
        if name in REQUESTS:
            keyword, position = "timeout", None
        elif name in TIMEOUT_ARGUMENT:
            keyword, position = TIMEOUT_ARGUMENT[name]
        else:
            return True
        if any(k.arg == keyword and not _is_none(k.value) for k in node.keywords):
            return True
        if any(k.arg is None for k in node.keywords):  # **options: cannot be shown, so refuse
            return False
        return position is not None and len(node.args) > position


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def scan_source(path: str, source: str) -> list[Finding]:
    tree = ast.parse(source)
    visitor = _Scan(path, _aliases(tree))
    visitor.visit(tree)
    return visitor.findings


def scan_tree() -> list[Finding]:
    found: list[Finding] = []
    for root in ROOTS:
        for file in sorted(root.rglob("*.py")):
            relative = file.relative_to(
                root.parents[1] if root.name == "abb_api" else root.parents[0]
            )
            found += scan_source(str(relative), file.read_text())
    return found


# (path, call) -> why it is safe and what bounds it. Reviewed like code.
ALLOWED: dict[tuple[str, str], str] = {
    ("src/abb_api/alerts/outbound.py", "asyncio.open_connection"): (
        "the opener of SafeHttpsSender; every send runs inside one total `asyncio.timeout` "
        "(timeout_seconds) that covers resolve, connect, TLS, write and read"
    ),
    ("src/abb_api/alerts/testnet.py", "asyncio.open_connection"): (
        "the development/test network file's opener, called by the same SafeHttpsSender and "
        "bounded by its total deadline; never configured in production"
    ),
    ("src/abb_api/backup/create.py", "subprocess.Popen"): (
        "operator CLI, not a serving path: streams pg_dump whose duration is proportional to "
        "the database, so no fixed timeout is right; killed on any error, bounded by the "
        "operator's or scheduler's job deadline"
    ),
    ("src/abb_api/backup/crypto.py", "subprocess.Popen"): (
        "operator CLI: the openssl encrypting filter fed by the dump above; same bound"
    ),
    ("src/abb_api/backup/crypto.py", "subprocess.run"): (
        "operator CLI: openssl decrypt of a bundle of unknown size; same bound"
    ),
    ("src/abb_api/backup/pgtools.py", "subprocess.Popen"): (
        "operator CLI: pg_restore / psql runs, proportional to the data; same bound"
    ),
    ("src/blackbox/coding.py", "subprocess.Popen"): (
        "the SDK's recorded shell command: `proc.wait(timeout=...)` follows, then the process "
        "group is killed (the command's own timeout is a required argument)"
    ),
    ("src/blackbox/policy_transport.py", "http.client.HTTPConnection"): (
        "makes no connection of its own: `conn.sock` is replaced by a deadline-enforcing socket "
        "opened with `socket.create_connection(timeout=...)` before any I/O"
    ),
}


def test_every_external_call_has_an_explicit_timeout() -> None:
    findings = scan_tree()
    unexpected = [f for f in findings if f.key not in ALLOWED]
    assert not unexpected, "calls without an explicit timeout:\n" + "\n".join(
        f"  {f.path}:{f.line} {f.call}" for f in unexpected
    )
    stale = set(ALLOWED) - {f.key for f in findings}
    assert not stale, f"allow-list entries that match nothing (delete them): {sorted(stale)}"


# ------------------------------------------------------------------ the scanner itself


def found(source: str) -> list[str]:
    return [f.call for f in scan_source("x.py", source)]


def test_flags_unbounded_calls() -> None:
    assert found("import httpx\nhttpx.AsyncClient()\n") == ["httpx.AsyncClient"]
    assert found("import httpx\nhttpx.get('u')\n") == ["httpx.get"]
    assert found("import httpx\nhttpx.Client(timeout=None)\n") == ["httpx.Client"]
    assert found("from httpx import AsyncClient as C\nC()\n") == ["httpx.AsyncClient"]
    assert found("import urllib.request\nurllib.request.urlopen('u')\n") == [
        "urllib.request.urlopen"
    ]
    assert found("import socket\nsocket.create_connection(('h', 1))\n") == [
        "socket.create_connection"
    ]
    assert found("import smtplib\nsmtplib.SMTP('h')\n") == ["smtplib.SMTP"]
    assert found("import subprocess\nsubprocess.run(['x'])\n") == ["subprocess.run"]
    assert found("import subprocess\nsubprocess.Popen(['x'])\n") == ["subprocess.Popen"]
    assert found("import asyncio\nasync def f():\n await asyncio.open_connection('h', 1)\n") == [
        "asyncio.open_connection"
    ]
    assert found("import requests\nrequests.get('u')\n") == ["requests.get"]
    assert found("import httpx\nhttpx.Client(**options)\n") == ["httpx.Client"]
    assert found("import asyncpg\nasyncpg.connect('dsn')\n") == ["asyncpg.connect"]


def test_accepts_explicit_timeouts_and_timeout_contexts() -> None:
    assert not found("import httpx\nhttpx.AsyncClient(timeout=5)\n")
    assert not found("import urllib.request\nurllib.request.urlopen('u', None, 3)\n")
    assert not found("import socket\nsocket.create_connection(('h', 1), 2)\n")
    assert not found("import smtplib\nsmtplib.SMTP('h', 25, 'me', 5)\n")
    assert not found("import subprocess\nsubprocess.run(['x'], timeout=5)\n")
    assert not found("import requests\nrequests.get('u', timeout=3)\n")
    context = (
        "import asyncio\nasync def f():\n"
        " async with asyncio.timeout(3):\n  await asyncio.open_connection('h', 1)\n"
    )
    assert not found(context)
    waited = (
        "import asyncio\nasync def f():\n"
        " await asyncio.wait_for(asyncio.open_connection('h', 1), 3)\n"
    )
    assert not found(waited)
    nested = (
        "import asyncio, httpx\nasync def f():\n async with asyncio.timeout(3):\n"
        "  async with httpx.AsyncClient():\n   pass\n"
    )
    assert not found(nested)


def test_a_timeout_context_does_not_leak_to_later_code() -> None:
    source = (
        "import asyncio, httpx\nasync def f():\n"
        " async with asyncio.timeout(3):\n  pass\n"
        " httpx.AsyncClient()\n"
    )
    assert found(source) == ["httpx.AsyncClient"]
