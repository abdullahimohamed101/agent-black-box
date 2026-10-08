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
   Capture is size-bounded: the first 1 MiB of each stream is kept, the rest is counted (`shell.stdout_bytes`) and flagged with `shell.output_truncated`; there is no marker inside the text.
5. **Test-result parsing** is by recognised summaries (`unittest`, `pytest`); an unrecognised runner yields no `test.*` attributes rather than guessed numbers.

## Consequences
- Old SDKs and servers keep working: every addition is an optional attribute (backward compatible, schema 1.0).
- The classifier is heuristic (a shell string is not fully parseable); its reasons are returned for tests and later policy work.
- Redaction stays best-effort (KI-032, KI-042): the value masker narrows but does not close the gap.
- **Behaviour change in the SDK:** `run.span(name, kind="shell")` now emits typed `shell.command.started/completed/failed` events (with `shell.command` = the span name, cut at 256 characters) instead of generic `span.*` events, so shell spans are first-class (spec §27). Code that relied on `span.*` for shell spans must switch kind to `custom`.
- Diffs of secret-looking paths (`.env*`, `*.pem`, keys, `.npmrc`...) and diffs over 1 MiB are not uploaded (`diff.withheld`); hashes and sizes are still recorded.

## Exactly what is and is not covered (review fixes, 2026-10-08)

**Sanitized (ANSI sequences removed, values of secret-looking environment variables masked, secret patterns redacted) before anything
is cut to length, hashed, uploaded or recorded:** the command (`shell.command`, the span name), the working directory (`shell.cwd`,
`test.suite`), `timeout.operation`, artifact names (fixed `stdout`/`stderr` for output; the redacted path for diffs; the SDK also redacts any
name it is given), stdout, stderr, diffs, and the text a tool returns to the model (`read_file` redacts by default; editing reads the real
content with `redact=False`). Patterns cover provider keys and tokens, JWTs, private keys, `Authorization`/`Basic`/`Bearer` headers, `-u user:pass`,
`--password/--token/--api-key` flags, `NAME=value` assignments whose name ends in a secret word (`AWS_SECRET_ACCESS_KEY`, `DB_PASSWORD`), and
URL credentials (`scheme://user:pass@`, `scheme://token@`). Letters-only keys (`AKIA...`) are caught (the pre-check scans any run of 16 letters).

**Files that hold secrets** (`.env*`, keys, `.npmrc`, `.netrc`, credentials files ...): their write/delete diffs are never uploaded; `git_diff`
excludes them with pathspecs and removes their hunks from any diff-shaped output; a command that names such a file (`cat .env`) has its
output withheld from the record and from the agent (`shell.output_withheld`).

**Not covered (known gaps):** a secret of unknown shape that is not an environment value, not in a recognised assignment and not in a
secret-named file (KI-032, KI-042); a secret built by the command at run time and printed in pieces; binary output; a command that reads a
secret file through a name that does not look like one (`cat config/prod.yml`); the command line itself when the secret is positional
(`mysql -p hunter2` is only caught if it looks like an assignment or flag).

**Classifier.** Handles redirections (`>`, `>>`, `&>`; `/dev/null` and `2>&1` are harmless), unquoted newlines, `( )`/`{ }`/`if`/`for`/`while`
groups, wrappers (`sudo`, `env`, `nohup`, `nice`, `time`, `timeout`, `xargs`, `watch` ...), `eval`, `sh/bash/zsh -c` including flag clusters (`-lc`),
`python -c`/`node -e` code that starts commands, awk `system()`, command substitution, git global options (`-C`, `-c`, `--git-dir`, ...) and
short-flag clusters (`push -fu`, `clean -xdf`), refspec deletes and history rewrites. Anything it cannot parse is classified R2 or higher,
and nesting beyond four levels is R3. **Gaps:** shell functions and aliases, variables used as commands (`$CMD`), here-documents with commands,
`find -exec` targets other than the whole find (always R3), and programs that run other programs internally (`make`, `npm run`, scripts):
those are R1 as "runs project code". Classification stays observe-only; a class is a hint, never a guarantee.

**Real-model example mode** runs arbitrary shell as the user with no sandbox. It requires `--i-understand-this-runs-commands`, the README warns,
recorded commands get a minimal environment without `HOME`, and git branch/remote names that look like flags are refused.
