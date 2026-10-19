# Package names: collision check (2026-10-19)

Checked against the public PyPI and npm registries. Nothing is published; this decides whether publishing is safe.

| Name | Where | Result |
| --- | --- | --- |
| `agent-black-box` (our SDK distribution name) | PyPI | free |
| `abb-event-schema` | PyPI | free |
| `abb-api` | PyPI | free |
| `@abb/event-schema` | npm | free |
| `agent-black-box` | npm | free |
| `blackbox` (our SDK **import** name) | PyPI | **taken**: an unrelated IRC-macros module, version 0.7.1, which also installs a top-level `blackbox` module |
| `agentblackbox` | PyPI | **taken**: another project, "A flight recorder for AI agents", version 0.2.0 |
| `agent-blackbox` | npm | **taken**: an unrelated tool, "A firewall for your AI coding agent", version 0.2.0 |

Two things follow, both reasons to decide before the first publication:

1. **Import-name clash.** Our SDK is imported as `blackbox`. A user who has the PyPI `blackbox` package installed in the same
   environment would have two distributions writing the same top-level module. Renaming the import package (for example to
   `agent_blackbox`) is a breaking change to the SDK, the examples, integrations, docs and tests; it is cheap now (nothing is
   published) and expensive later. Recommended: rename before any package is published.
2. **Name closeness.** `agentblackbox` on PyPI describes itself with almost our tagline. This is not a legal finding; it means
   people may confuse the two, so the README and site should keep leading with what ours records and how it is run, and the
   repository name stays the distinguishing one.

Until the owner decides, the README says "install from source" and no package is published.
