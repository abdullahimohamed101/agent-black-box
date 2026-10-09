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
   `*.gpg`, `kubeconfig`, `.docker/config.json`, `.kube/config`, `*secret*.json|yml|toml` ...; superseded below: see the third review round for the current limits). It extracts the secret values (dotenv and `NAME=VALUE` lines, JSON string leaves, PEM bodies,
   credential-file lines and URL passwords, older committed versions via `git show HEAD:`, `:` and the first stashes) and masks every occurrence, plus its
   base64 and URL-encoded forms, in stdout, stderr, diffs, events and the text returned to the model, **however the command read the file**.
   The minimums and the placeholder rules changed in the third round (below).
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
inline code with file, process or network access, `eval` of dynamic content, `bash <(...)` and piping content into an interpreter are R3; running a script by path (`bash x.sh`, `python3 script.py`) is R2 (unknown effect). Destructive and cloud commands are classified by name
(`terraform destroy`, `aws ... rm|delete`, `gcloud ... delete`, `kubectl delete`, `npm publish`, `redis-cli flushall`, SQL `DELETE|DROP|TRUNCATE`, disk tools, `mv / x`, `truncate`,
`git rm -rf`, `git tag -d`, `curl -X DELETE`, ...). It still cannot see inside scripts, functions, aliases or variables used as commands; those are R2 or higher.
A class is a hint, never a guarantee, and nothing is enforced.

**Real-model example mode** runs arbitrary shell as the user with no sandbox. It requires `--i-understand-this-runs-commands`, the README warns, recorded commands get a
minimal environment without `HOME`, and git branch/remote names that look like flags are refused.

## Third review round (2026-10-08): what changed, and the gaps we accept

An independent adversarial pass (about 650 commands and many encodings against a real recorder and a capturing server) found no P0 and these defects in the
value-masking design, all fixed with regression tests (`tests/test_secret_round3.py`, `tests/test_secret_leaks.py`):

- **Forgotten values.** Values are now remembered for the life of the recorder: moving, copying then deleting, or renaming a secret file no longer unmasks it.
- **Placeholders corrupted the agent's view.** `SECRET_KEY=secret`, `API_TOKEN=test`, `COOKIE_SECURE=true` in a sample file turned ordinary words into masks (source code,
  `Ran 2 tests`). Template files (`.example`, `.sample`, `.template`, `.dist`) and common words, booleans, numbers and placeholders (`changeme`, `<token>`, `${X}`,
  `xxxx`) are not learned; a real-looking value in a template file still is. Values under 3 characters, and under 6 or 8 for names that do not look secret, are not masked.
- **History.** Secret values that only exist in older commits, rotated or deleted files, are learned from history (bounded, cached by `HEAD`) and masked by value, so
  `git show <blob>` of a deleted `.env` prints nothing secret. (`git cat-file` and `git show` are additionally *withheld* while a sensitive file exists in the tree; once none
  does, value masking is the only defence for objects that hold values it did not learn.)
- **Remotes and `.git/config`** are scanned; `https://<token>@host/` (a bare token as the user, as Azure DevOps uses) is a credential.
- **The host's own environment** is masked by value for every variable except a short benign list and values that are plainly harmless (paths, plain URLs, numbers,
  ordinary words), because `ps eww -p $PPID` prints the whole environment and a name like `SMTP_PW` or `HF_TOK` cannot be recognised reliably.
- **Discovery.** Only `.git`, real package trees (`node_modules`, `site-packages`, `__pycache__`) are skipped, and never a directory that contains a file with a sensitive
  name; `build/`, `dist/`, `target/`, `.cache/` and deep paths are scanned. Up to 5,000 secret files of 4 MiB each, within a 2 s time budget; when the budget is hit `SecretFiles.incomplete`
  is set and the recorder logs a single value-free warning (it is not an event attribute: the event schema has no slot for it yet), instead of being silently partial. Values of any length are learned (long ones by exact match plus head and tail).
- **File shapes.** Raw single-token files (`master.key`, `*.ppk`, `*.gpg`), YAML block scalars and list items, multi-line quoted values, `value # comment`, and every string leaf of
  a file that is secret by name (`secrets.json`, `credentials.*`, `creds.*`, `*.tfvars`).
- **Encodings.** base64 at all three byte alignments (and url-safe), percent-encoding variants, JSON, `repr`, `shlex`, HTML/XML and backslash escapes, hex, and any
  12-character head or tail of a long value.
- **The example's `search` tool** returned file lines to the model unmasked, also through symlinks: it now masks, and does not follow links to secret files or out of the repo.
- **Redaction no longer rewrites comparisons** (`password == 'test'` became `password =[REDACTED]`).
- **Performance.** Two quadratic slowdowns (a quoted-value regex: 80,000 characters took 36 s; the classifier's reasons tuple: a 200 KB command took 21 s) are fixed and
  guarded by time-bounded tests.
- **Classifier.** `env -S`, process substitution, `/dev/tcp`, pipes into interpreters, `git rebase --exec`, forced ref moves, `npm run <unknown>`, `npm exec`, `go run`, writer
  flags on read-only tools (`sed --in-place=`, `awk -i inplace`, `tree --output`, `less -o`, `sort --compress-program`, `file -C`, `find -fprint0`) and more; a second table
  asserts that ordinary commands (`npm test`, `git tag`, `ls 2>&1`, `diff <(a) <(b)`, `python -m json.tool`) stay low.
- **Event attributes** built from user text (`file.path`, `git.branch`, `git.push_target`) are sanitized, and the leak tests now assert that event batches really reached the
  capturing server, so they cannot go blind to attributes.

### Known gaps (accepted; masking cannot solve these)

1. **Reversible transforms computed by a command that avoids a sensitive name:** hex, base32, rot13, reversing, lower-casing, splitting a value across `echo`s or across stdout
   and stderr, inserting separators, control or zero-width characters between characters, NFC/NFD changes. The command computes something that is no longer the value.
2. **Secrets created, changed or removed inside a single command**, runtime-generated tokens, and values overwritten and restored mid-command: the scan sees the workspace at the
   start and end of a command, not during it.
3. **Secrets in files that are neither named like secrets nor matched by a pattern** (`config.py`, `main.tf`, `settings.json` without secret-looking keys, SQLite and other binary files).
4. **Secrets outside the workspace** read by absolute path or `~` (`cat ~/.ssh/id_*`, `~/.aws/*`): caught by name, and real PEM keys and provider-shaped keys by pattern, nothing else.
   Linux-only vectors (`/proc/$PPID/environ`) were not exercised on the macOS host.
5. **The model typing a secret it was never shown**, and secrets split across 64 KiB chunks of a *non-text* stream.

**What an operator should do:** capture is opt-in (`PayloadMode.FULL`); keep real credentials out of the agent's reach (a separate user or container, a throwaway working
directory, no production keys in the environment or the repo); treat the stored artifacts as sensitive anyway (KI-040 retention, KI-042 no server-side scan).

## Fourth review round (2026-10-09): what changed, and the gaps added

A fourth independent pass (new attacks only, against the third-round logic) found no P0. Fixed, with regression tests (`tests/test_secret_round3.py`, section 13):

- **Private keys kept on one line** (a GCP service-account JSON, a quoted `.env` value with `\n` escapes): every line of the key is learned, not only the whole blob and its ends.
- **Webhook and token-in-path values** were dismissed as plain URLs or paths. A URL or path is harmless only if it has no token-like segment (8+ characters mixing letters and digits, or
  over 40 characters), no query and no userinfo; this applies to files and to the host environment.
- **Placeholder rules** (`password123`, `changeme2024`, `dummy...`, `sample...`) now apply only to template files; in a real `.env` a weak password is still somebody's password. Numbers under a
  secret-looking key (a PIN) are learned.
- **Redaction**: `'password' => 'x'` (Ruby/PHP) is redacted again; a quoted literal after `==`/`===` on a secret-named variable is hidden (`assert password == "x"` keeps its operator).
  Comparisons without a literal (`password == other`) and arrow functions are untouched.
- **JSON inside one dotenv value** teaches its parts; a UTF-8 BOM no longer hides the first key; a secret file over the size cap marks the scan incomplete (one value-free warning); a learned
  value is dropped as "an ordinary word of the repo's source" only when it is a whole word there, not a substring; the example agent masks its tool error text.

**Accepted gaps added** (not fixed; measured):

- **Single-line structured values**: only whole values and JSON are taught, not sub-tokens of `USERS=bob:PW,al:PW2`, base64 `Authorization: Basic ...`, `.tfvars` maps and lists, YAML flow collections.
- **Learn-gaps**: values under the 6/8-character minimums, numeric JSON leaves, a custom `extraheader` name, `git config alias` shell strings, INI `value ; comment`, unreachable git objects (a dangling blob), and a rewrite of an
  already-cached secret file that keeps the same size and mtime.
- **Host environment**: names in `PYTHON*`, `LC_*`, `XDG_*`, `CONDA_*`, `NVM_*`, `RUNNER_*`, `APPLE_*`, `TERM_*`, `NODE_ENV`, `MAIL`, `EDITOR`, `PAGER`, `PS1` are never masked; values of non-secret names under 8 characters;
  10-digit numbers; variables added to `os.environ` or passed as `run_command(env=...)` after the recorder was created.
- **Redaction false positives** that can corrupt source the model reads (`token := f()`, `password: str`, `f(password=pw)`); `edit_file` reads unredacted content, so edits are unaffected.
- **Cost**: masking 3,000 learned values over a 4 MiB output takes about 3.5-5 s (one large alternation); a 90,000-file tree costs about 1.9 s per command; the scan budget is 2 s per refresh.
- **Classifier**: a command over 40 KiB with a dangerous command in the middle is classified R2 ("very large command"); `env -iS`, `env -P`, and a process substitution containing nested parentheses can be
  under-classified. The risk class gates nothing in capture; it is an observability hint.

