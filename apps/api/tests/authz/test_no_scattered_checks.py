"""Permission decisions live in `authz/` (spec §91). Nothing else may look at roles or scopes."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "abb_api"
FORBIDDEN_ATTRIBUTES = {"role", "scopes", "actions"}
# Where reading a role or scope is the job: the authorization package, authentication, the CLI
# that provisions keys and members, and the membership repository.
ALLOWED = ("authz/", "auth/", "cli.py", "workspaces/repository.py")


def findings(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename)
    return [
        f"{filename}:{node.lineno}: .{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES
    ]


def test_no_module_outside_authz_inspects_roles_scopes_or_actions() -> None:
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith(ALLOWED[:2]) or relative in ALLOWED[2:]:
            continue
        found.extend(findings(path.read_text(), relative))
    # `actions.RUN_READ` (the vocabulary module) is a Name, not a forbidden attribute access.
    assert found == []


def test_the_scan_catches_the_patterns_it_exists_for() -> None:
    assert findings("if principal.role == 'ADMIN': pass", "x.py")
    assert findings("ok = 'runs:read' in principal.scopes", "x.py")
    assert findings("allowed = principal.actions", "x.py")
    assert not findings("from abb_api.authz import actions\nx = actions.RUN_READ", "x.py")


def test_no_router_still_calls_require_principal() -> None:
    offenders = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "require_principal(" in p.read_text()
    ]
    assert offenders == []
