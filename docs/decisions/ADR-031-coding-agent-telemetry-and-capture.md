# ADR-031: Coding-Agent Telemetry Vocabulary, Command Risk Classes and Secret-Safe Capture

Status: Accepted (implemented in Phase 6)
Date: 2026-10-07

## Context
Spec §26, §27, §82 and §83 ask for file, Git, shell and test events, command classification, and "sensitive environment variables never
captured". The registry already has `file.*`, `git.*`, `shell.command.*` and attributes for paths, hashes, exit codes and tests. What is missing is how
output and diffs are attached, how tests and risk are represented, and who is responsible for keeping secrets out.

## Decision
1. **Event vocabulary (no new event types).** New optional known attributes: `diff.artifact`, `file.operation`, `shell.category`, `shell.stdout_artifact`,
   `shell.stderr_artifact`, `shell.stdout_bytes`, `shell.stderr_bytes`, `shell.output_truncated`, `test.suite`, `git.diff_stat_files`. A test run is a
   `shell.command.*` span whose closing event carries `test.*` attributes (framework, suite, total, passed, failed, skipped, `failing` ids): the test is a
   semantic fact about a command, not a second event, so spans and durations stay single. A non-zero exit closes with `shell.command.failed` (`status=error`).
2. **Content is never in events.** File reads record path, size and hash only. Writes record before/after size and hash, line counts and a `diff.artifact`
   (a unified diff), never whole files (§82.1). Shell events carry the command, cwd, duration, exit code, risk class and artifact ids for stdout and stderr.
3. **Risk classes R0-R4 (observe-only)** are assigned by a deterministic classifier in the SDK (`blackbox.coding.classify_command`) and written as `shell.risk_class`
   plus the spec §27 category (`READ_ONLY|MODIFY_FILES|NETWORK|PACKAGE_INSTALL|PROCESS_CONTROL|DESTRUCTIVE`) as `shell.category`. Compound commands
   (`;`, `&&`, `|`) take the highest segment; `sudo` and `curl | sh` raise the class; unknown executables are R1 (assumed local mutation), never R0.
   A user-supplied classifier overrides the default (spec §83). Nothing blocks a command; enforcement is a later, explicit integration mode.
4. **Secret-safe capture is the SDK's job, in three layers.** (a) The environment is never read into events, and commands run with a minimal allowlisted environment so
   the agent's own credentials do not reach subprocess output. (b) Captured text passes the SDK redaction patterns (`Redactor.redact_text`, whole-text so multi-line
   private keys are caught) and a **value masker**: any value of an environment variable whose name looks secret (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, ...) is replaced
   by `[REDACTED:env]` wherever it appears, which also covers secrets of unknown shape. (c) Only then is the text hashed and uploaded; the hash is of the stored (redacted) bytes.
   Capture is size-bounded (default 1 MiB per stream, then a truncation marker).
5. **Test-result parsing** is by recognised summaries (`unittest`, `pytest`); an unrecognised runner yields no `test.*` attributes rather than guessed numbers.

## Consequences
- Old SDKs and servers keep working: every addition is an optional attribute (backward compatible, schema 1.0).
- The classifier is heuristic (a shell string is not fully parseable); its reasons are returned for tests and later policy work.
- Redaction stays best-effort (KI-032, KI-042): the value masker narrows but does not close the gap.
- **Behaviour change in the SDK:** `run.span(name, kind="shell")` now emits typed `shell.command.started/completed/failed` events (with `shell.command` = the span name, cut at 256 characters) instead of generic `span.*` events, so shell spans are first-class (spec §27). Code that relied on `span.*` for shell spans must switch kind to `custom`.
- Diffs of secret-looking paths (`.env*`, `*.pem`, keys, `.npmrc`...) and diffs over 1 MiB are not uploaded (`diff.withheld`); hashes and sizes are still recorded.
