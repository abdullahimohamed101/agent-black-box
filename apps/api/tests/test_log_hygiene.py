"""No log call can carry user data (INV-5, spec §116.2): a parse of every `logging` call site.

The sink already keeps exception messages out (`JsonFormatter`); this scan keeps the *call sites*
honest, so a different handler, a test capture or a future formatter change cannot start leaking.
A call fails when it:

* has a message that is not a fixed string (an f-string, `%`, `.format`, a variable);
* passes an exception object (anything bound by `except ... as e`) other than as `type(e)`;
* asks for a traceback (`exc_info=` truthy, `.exception(...)`) outside the reviewed allow-list;
* interpolates or passes a value named like a body, token, secret, key, address, header, cookie,
  url, e-mail, DSN, prompt or command (identifier words, split on underscores);
* reads `request.url`, `.headers`, `.cookies`, `.client` or `.query_params`;
* uses an `extra=` key that names such data (`path`, `url`, `address`, `email`, ...).

Exceptions go in `ALLOWED` with the reason, reviewed like code; an entry that no longer matches
anything fails too, so the list cannot rot. The scanner is itself tested on planted violations.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "abb_api"

LEVELS = {"debug", "info", "warning", "warn", "error", "critical", "exception", "log"}
# Words that name data which must not be logged; matched against identifier words.
SENSITIVE_WORDS = {
    "body",
    "payload",
    "token",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "key",
    "dsn",
    "url",
    "uri",
    "address",
    "ip",
    "email",
    "cookie",
    "cookies",
    "header",
    "headers",
    "authorization",
    "prompt",
    "command",
    "cmd",
    "query",
    "credentials",
}
SENSITIVE_EXTRA_KEYS = {"path", "client", "query_params"} | SENSITIVE_WORDS
REQUEST_ATTRIBUTES = {"url", "headers", "cookies", "client", "query_params", "body"}
REQUEST_NAMES = {"request", "req", "scope", "websocket"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.path, self.rule, self.detail)


def _words(identifier: str) -> set[str]:
    return {w for w in re.split(r"[_\W]+", identifier.lower()) if w}


def _level_attributes(value: ast.expr, loggers: set[str]) -> list[str]:
    """The logger level attributes an expression can evaluate to (``logger.warning``, or either
    branch of ``logger.error if loud else logger.info``); [] for anything else."""
    if isinstance(value, ast.IfExp):
        return _level_attributes(value.body, loggers) + _level_attributes(value.orelse, loggers)
    if isinstance(value, ast.Attribute) and value.attr in LEVELS:
        owner = value.value
        if isinstance(owner, ast.Name) and (owner.id in loggers or owner.id == "logging"):
            return [value.attr]
        if (
            isinstance(owner, ast.Call)
            and isinstance(owner.func, ast.Attribute)
            and owner.func.attr == "getLogger"
        ):
            return [value.attr]
    return []


def _logger_aliases(tree: ast.Module, loggers: set[str]) -> dict[str, set[str]]:
    """Names bound to a logger method (``emit = logger.warning``) and the levels they can be."""
    aliases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            levels = _level_attributes(node.value, loggers)
            if levels:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases.setdefault(target.id, set()).update(levels)
    return aliases


def _is_wrapper_name(name: str) -> bool:
    return name == "_log" or name.startswith(("log_", "_log_"))


def _is_log_call(node: ast.Call, loggers: set[str], aliases: dict[str, set[str]]) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id in aliases:
        return True
    if isinstance(func, ast.Attribute) and func.attr in LEVELS:
        owner = func.value
        if isinstance(owner, ast.Name):
            return owner.id in loggers or owner.id == "logging"
        # logging.getLogger(__name__).warning(...)
        return (
            isinstance(owner, ast.Call)
            and isinstance(owner.func, ast.Attribute)
            and owner.func.attr == "getLogger"
        )
    # `log = logger.error if ... else logger.warning; log(...)`
    return isinstance(func, ast.Name) and func.id in loggers and func.id == "log"


def _logger_names(tree: ast.Module) -> set[str]:
    names = {"logger", "log", "_logger"}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "getLogger"
        ):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


class _Scan(ast.NodeVisitor):
    def __init__(self, path: str, loggers: set[str], aliases: dict[str, set[str]]) -> None:
        self.path = path
        self.loggers = loggers
        self.aliases = aliases
        self.findings: list[Finding] = []
        self._scope: list[str] = []
        self._excepts: list[str] = []

    def _add(self, node: ast.AST, rule: str, detail: str) -> None:
        line = getattr(node, "lineno", 0)
        self.findings.append(Finding(self.path, line, rule, f"{self._where()}:{detail}"))

    def _where(self) -> str:
        return ".".join(self._scope) or "<module>"

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._excepts.append(node.name)
        self.generic_visit(node)
        if node.name:
            self._excepts.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if _is_log_call(node, self.loggers, self.aliases):
            self._check(node)
        else:
            self._check_wrapper(node)
        self.generic_visit(node)

    def _check_wrapper(self, node: ast.Call) -> None:
        """A helper such as ``self._log(msg)`` or ``log_event(msg)`` hides the logger call, so its
        message argument must be as fixed as a direct call's."""
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if not _is_wrapper_name(name) or not node.args:
            return
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            self._add(node, "wrapper-message", f"{name}({ast.unparse(first)[:40]})")

    def _check(self, node: ast.Call) -> None:
        func = node.func
        aliased = self.aliases.get(func.id, set()) if isinstance(func, ast.Name) else set()
        if (isinstance(func, ast.Attribute) and func.attr == "exception") or "exception" in aliased:
            self._add(node, "traceback", "exception()")
        for keyword in node.keywords:
            if keyword.arg == "exc_info" and not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value in (False, None)
            ):
                self._add(node, "traceback", "exc_info")
        level_call = (isinstance(func, ast.Attribute) and func.attr == "log") or aliased == {"log"}
        message_at = 1 if level_call else 0
        if len(node.args) > message_at:
            message = node.args[message_at]
            if not (isinstance(message, ast.Constant) and isinstance(message.value, str)):
                self._add(node, "message", ast.unparse(message)[:60])
            expressions = list(node.args[message_at + 1 :])
        else:
            expressions = []
        for keyword in node.keywords:
            if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                for key, value in zip(keyword.value.keys, keyword.value.values, strict=True):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        if key.value in SENSITIVE_EXTRA_KEYS or _words(key.value) & {
                            "token",
                            "secret",
                            "password",
                            "cookie",
                            "email",
                            "address",
                            "url",
                        }:
                            self._add(node, "extra-key", key.value)
                        expressions.append(value)
                    else:  # **spread: cannot be shown
                        expressions.append(value)
            elif keyword.arg == "extra":
                expressions.append(keyword.value)
        for expression in expressions:
            self._expression(node, expression)

    def _expression(self, call: ast.Call, root: ast.expr) -> None:
        skip: set[int] = set()
        for node in ast.walk(root):
            # type(exc) / type(exc).__name__ is the one way an exception may be mentioned.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "type"
            ):
                skip |= {id(n) for n in ast.walk(node)}
        for node in ast.walk(root):
            if id(node) in skip:
                continue
            if isinstance(node, ast.Name):
                if node.id in self._excepts:
                    self._add(call, "exception-value", node.id)
                elif _words(node.id) & SENSITIVE_WORDS:
                    self._add(call, "sensitive-name", node.id)
            elif isinstance(node, ast.Attribute):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id in REQUEST_NAMES
                    and node.attr in REQUEST_ATTRIBUTES
                ):
                    self._add(call, "request-object", f"{node.value.id}.{node.attr}")
                elif _words(node.attr) & SENSITIVE_WORDS:
                    self._add(call, "sensitive-name", node.attr)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in ("path", "headers", "client", "query_string")
            ):
                self._add(call, "request-object", f"get({node.args[0].value!r})")


def scan_source(path: str, source: str) -> list[Finding]:
    tree = ast.parse(source)
    loggers = _logger_names(tree)
    visitor = _Scan(path, loggers, _logger_aliases(tree, loggers))
    visitor.visit(tree)
    return visitor.findings


def scan_tree() -> list[Finding]:
    found: list[Finding] = []
    for file in sorted(ROOT.rglob("*.py")):
        found += scan_source(f"src/abb_api/{file.relative_to(ROOT)}", file.read_text())
    return found


# (path, rule, "function:detail") -> why it is safe. Reviewed like code.
_SCRUBBED = (
    "the traceback is rendered by JsonFormatter.safe_traceback: frames and exception class names "
    "only, never the message, so this cannot carry user data; the stack is the evidence a "
    "crash needs"
)
ALLOWED: dict[tuple[str, str, str], str] = {
    ("src/abb_api/auth/login.py", "exception-value", "LoginService.start:exc"): (
        "only _reason(exc) is logged: a member of the closed OidcError reason set, or the fixed "
        "string provider_unavailable; the exception text is never read"
    ),
    ("src/abb_api/auth/login.py", "exception-value", "LoginService.finish:exc"): (
        "only _reason(exc) is logged: a member of the closed OidcError reason set, or the fixed "
        "string provider_unavailable; the exception text is never read"
    ),
    ("src/abb_api/auth/service.py", "sensitive-name", "authenticate:key_id"): (
        "the key id is the public half of an API key (the secret is the other half and is never "
        "read here); parse_key accepts only a fixed-length pattern, so it is bounded and opaque"
    ),
    ("src/abb_api/auth/sessions.py", "traceback", "_audit:exc_info"): _SCRUBBED,
    (
        "src/abb_api/core/middleware.py",
        "traceback",
        "RequestContextMiddleware.__call__:exception()",
    ): (_SCRUBBED),
    ("src/abb_api/ingestion/service.py", "traceback", "IngestionService.ingest_batch:exc_info"): (
        _SCRUBBED
    ),
    ("src/abb_api/jobs/worker.py", "traceback", "Worker._keep_waiting_leases:exc_info"): _SCRUBBED,
    ("src/abb_api/jobs/worker.py", "traceback", "Worker.run_forever:exception()"): _SCRUBBED,
    ("src/abb_api/jobs/worker.py", "traceback", "Worker._keep_lease:exc_info"): _SCRUBBED,
    ("src/abb_api/jobs/worker.py", "traceback", "Worker._record_failure:exc_info"): _SCRUBBED,
    ("src/abb_api/retention/runner.py", "traceback", "run_forever:exception()"): _SCRUBBED,
    ("src/abb_api/retention/tick.py", "exception-value", "_Enforcement.workspace:error"): (
        "only error.tier.value is logged: the tier is a member of a fixed enum"
    ),
    ("src/abb_api/retention/tick.py", "exception-value", "_workspace:error"): (
        "only error.tier.value is logged: the tier is a member of a fixed enum"
    ),
}


def test_no_unreviewed_log_call_can_carry_user_data() -> None:
    findings = scan_tree()
    unreviewed = sorted(
        f"{f.path}:{f.line} {f.rule} {f.detail}" for f in findings if f.key not in ALLOWED
    )
    assert not unreviewed, (
        "log calls that could carry user data (log the exception class with error_type, a fixed "
        "reason code, or an opaque id; or add a reviewed ALLOWED entry):\n" + "\n".join(unreviewed)
    )


def test_every_allowed_entry_still_matches() -> None:
    present = {f.key for f in scan_tree()}
    stale = sorted(set(ALLOWED) - present)
    assert not stale, f"ALLOWED entries that match nothing (remove them): {stale}"
    assert all(len(reason) > 30 for reason in ALLOWED.values()), "every entry says why"


# ------------------------------------------------------------------ the scanner itself


def _rules(source: str) -> set[str]:
    return {f.rule for f in scan_source("x.py", source)}


def test_the_scanner_catches_planted_violations() -> None:
    header = "import logging\nlogger = logging.getLogger(__name__)\n"
    cases = {
        "message": "def f(v):\n    logger.info(f'got {v}')\n",
        "exception-value": (
            "def f():\n    try:\n        pass\n    except Exception as exc:\n"
            "        logger.warning('x', extra={'why': str(exc)})\n"
        ),
        "traceback": "def f():\n    logger.warning('x', exc_info=True)\n",
        "sensitive-name": "def f(body):\n    logger.info('x', extra={'n': len(body)})\n",
        "request-object": "def f(request):\n    logger.info('x', extra={'n': request.headers})\n",
        "extra-key": "def f():\n    logger.info('x', extra={'address': 1})\n",
    }
    for rule, source in cases.items():
        assert rule in _rules(header + source), rule
    assert "traceback" in _rules(header + "def f():\n    logger.exception('x')\n")
    assert "message" in _rules(header + "def f(v):\n    logging.getLogger('a').error(v)\n")
    assert "sensitive-name" in _rules(
        header + "def f(api_key):\n    logger.info('x %s', api_key)\n"
    )


def test_the_scanner_accepts_the_safe_forms() -> None:
    source = (
        "import logging\nlogger = logging.getLogger(__name__)\n"
        "def f(job, count):\n"
        "    try:\n        pass\n    except Exception as exc:\n"
        "        logger.warning('failed', extra={'error_type': type(exc).__name__, 'n': count})\n"
        "    logger.info('done', extra={'job_id': str(job.id), 'exc_info_not': 1})\n"
        "    logger.info('x', exc_info=False)\n"
    )
    assert scan_source("x.py", source) == []


def test_an_aliased_logger_method_is_still_a_logger_call() -> None:
    header = "import logging\nlogger = logging.getLogger(__name__)\n"
    aliased = header + "emit = logger.warning\ndef f(v):\n    emit(f'got {v}')\n"
    assert "message" in _rules(aliased)
    in_function = header + "def f(v):\n    emit = logger.error\n    emit('x', extra={'body': v})\n"
    assert "extra-key" in _rules(in_function)
    conditional = (
        header + "def f(v, loud):\n    out = logger.error if loud else logger.info\n    out(v)\n"
    )
    assert "message" in _rules(conditional)
    exception_alias = header + "def f():\n    out = logger.exception\n    out('x')\n"
    assert "traceback" in _rules(exception_alias)
    getter = "import logging\nemit = logging.getLogger('a').warning\ndef f(v):\n    emit(v)\n"
    assert "message" in _rules(getter)
    safe = header + "emit = logger.warning\ndef f():\n    emit('fixed', extra={'n': 1})\n"
    assert scan_source("x.py", safe) == []


def test_a_logging_wrapper_with_a_non_literal_message_is_flagged() -> None:
    cases = (
        "class A:\n    def f(self, v):\n        self._log(v)\n",
        "class A:\n    def f(self, v):\n        self._log(f'x {v}')\n",
        "def f(v):\n    log_event(v)\n",
        "def f(v):\n    log_failure('x' + v)\n",
    )
    for source in cases:
        assert "wrapper-message" in _rules(source), source
    for safe in (
        "class A:\n    def f(self):\n        self._log('fixed text')\n",
        "def f():\n    log_event('fixed text', 3)\n",
        "def f(v):\n    login_user(v)\n    catalog(v)\n",  # names that merely start with "log"
    ):
        assert "wrapper-message" not in _rules(safe), safe
