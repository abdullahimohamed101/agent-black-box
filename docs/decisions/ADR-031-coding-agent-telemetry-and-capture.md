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

## Design after the second review: know the secret values, mask them everywhere (2026-10-08)

**Honest scope first.** Capture is best effort. It only happens when the application opted into `PayloadMode.FULL`; in the default mode nothing
is uploaded. Nothing here can promise that no secret is ever stored: unknown-shape secrets that are in no environment variable and no
recognisable secret file remain a risk (KI-032, KI-042 stay open). What follows is what the code does, and what it does not.

**Why not a command blocklist.** Every review round found another shape that printed a secret file (`cat<.env`, `bash -c`, `grep -r`,
`git show HEAD:.env`, a coloured diff header). A blocklist of command shapes cannot be complete, so the primary defence is by *value*:

1. **Value masking from sensitive files** (`blackbox/secretscan.py`). Before and after every recorded command (and before each file diff and
   `read_file`), the recorder rescans the workspace for sensitive files (names matched case-insensitively: `.env*`, `*.env`, `.envrc`, `*.pem`,
   `*.key`, `id_*`, `.netrc`, `.npmrc`, `.pypirc`, `.pgpass`, `.htpasswd`, `.git-credentials`, `credentials*`, `*.tfstate`, `*.tfvars`, `*.jks`, `*.ppk`,
   `*.gpg`, `kubeconfig`, `.docker/config.json`, `.kube/config`, `*secret*.json|yml|toml` ...; `.git`, `node_modules` and virtualenvs are skipped; at most
   400 files of 256 KiB, cached by size and mtime). It extracts the secret values (dotenv and `NAME=VALUE` lines, JSON string leaves, PEM bodies,
   credential-file lines and URL passwords, older committed versions via `git show HEAD:`, `:` and the first stashes) and masks every occurrence, plus its
   base64 and URL-encoded forms, in stdout, stderr, diffs, events and the text returned to the model, **however the command read the file**.
   Values of keys that look secret are masked from three characters; other values in those files only if token-like (8+ characters with a digit or symbol).
2. **Environment values** (names containing KEY, TOKEN, SECRET, PASSWORD, PAT, PASS, PWD, AUTH, DSN, COOKIE, SESSION ... and the SDK's own API key) are masked the
   same way; the child process gets no inherited credentials and no `HOME`.
3. **Pattern redaction** of the remaining text: provider keys and tokens (glued to preceding or following characters too), JWTs, private keys, Authorization/Basic/Bearer
   headers, cookies, signed-URL parameters, `-u user:pass`, `--password` flags, `mysql -p`, `sshpass -p`, `docker login -p`, npm auth tokens, quoted `"password": "..."`
   keys, `NAME=value` assignments, and URL credentials (including an empty user). ANSI sequences are removed first.
4. **Order.** Everything is sanitized *before* any truncation (the 256-character command attribute, the 1 MiB capture, `read_file`'s limit, artifact names), so a cut
   cannot leave a secret's prefix. The capture reads 16 KiB past the limit for that purpose. Output past the limit is counted (`shell.stdout_bytes`) and flagged
   (`shell.output_truncated`); there is no marker inside the text.
5. **Path-based withholding, shell-aware, as defence in depth.** `command_may_reach_secrets` tokenizes the command (operators, redirections, `$( )`, backticks,
   newlines), recurses into `sh/bash -c` and `python -c`/`node -e`/`ruby -e` bodies, matches globs against the secret files that exist, understands `REV:path`
   arguments, and treats recursive or dynamic readers (`grep -r`, `find -exec`, `xargs`, `tar`, `git grep|archive|cat-file`, `source`, `eval`, scripts) as reaching
   a secret file whenever one exists. When it says yes, the output is not stored and not returned to the agent (`shell.output_withheld = may_reach_secrets`).
   Diffs are filtered per file section (`diff --git`, `--cc`, `--combined`, plain `---/+++`, any prefix or none, coloured or not).

**Does not cover:** a secret in a file that is not named like a secret and is not in the environment; a value the command computes or receives from the network and
prints; secrets shorter than the minimums; binary output; secrets split across lines or across stdout and stderr; files outside the workspace root that a command
reads (`cat ~/.aws/credentials` is caught by name only); the model *typing* a secret it was never shown. The value scan sees the workspace as it is at command start and end,
not mid-command.

**Classifier (observe-only).** Default inverted: R0 only for allowlisted read-only commands used without write-capable flags (`sort -o`, `uniq in out`, `tree -o`, `xxd -r`,
`date -s`, `hostname NAME`, `rg --pre`, `sed w/e`, `git ... --output`, `git grep -O`, `git -c core.pager=` and similar are not R0); any unrecognised command or flag combination
is R2 ("unknown, may modify anything"); known test and build invocations (`python -m unittest|pytest`, `pytest`, `npm test`, `cargo test`, `make test`, `uv run ...`) are R1;
scripts, inline code with file, process or network access, `eval` of dynamic content and `bash <(...)` are R3. Destructive and cloud commands are classified by name
(`terraform destroy`, `aws ... rm|delete`, `gcloud ... delete`, `kubectl delete`, `npm publish`, `redis-cli flushall`, SQL `DELETE|DROP|TRUNCATE`, disk tools, `mv / x`, `truncate`,
`git rm -rf`, `git tag -d`, `curl -X DELETE`, ...). It still cannot see inside scripts, functions, aliases or variables used as commands; those are R2 or higher.
A class is a hint, never a guarantee, and nothing is enforced.

**Real-model example mode** runs arbitrary shell as the user with no sandbox. It requires `--i-understand-this-runs-commands`, the README warns, recorded commands get a
minimal environment without `HOME`, and git branch/remote names that look like flags are refused.
