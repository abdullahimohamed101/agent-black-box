# ADR-131: Security Detectors and the `security` Findings Category

Status: Accepted (Phase 13, step 2). Implements D1, D2, D3 and D5 of the Phase 13 plan; extends ADR-080 (finding contract), ADR-081
(severity and health), ADR-101 (search is never an oracle) and ADR-130 (the redaction records the credential detector reads).
Date: 2026-10-13
Phase: 13

## Context

After a run an engineer should be able to see whether the agent touched secrets, ran something destructive or contacted somewhere unexpected,
with each claim linked to the events that show it. The detectors must read content (a command, a path, a URL) to decide, but a finding is
visible to every role that may read the run, including those that may not read content (ADR-061). A finding that repeated a path or a host
would turn the findings list into a side channel around withholding. False positives are the other risk: an alarm that cries wolf stops
being read.

## Decision

1. **Six detectors in the Phase 10 framework**, category `security`, detector ids `sec.<name>`, kind `probable`, rule version `1.0.0` each
   (recorded as `detector_version`, e.g. `sec.risky_git@1.0.0`). They run in the same `detect_findings` job, with the same debounce, fingerprints,
   dedupe, per-detector and per-run bounds and rebuild as the reliability detectors (`DETECTORS_VERSION` becomes 2, so stored analyses read
   `outdated` until `rebuild-findings`). One finding per detector per run aggregates every matching event: `count` (events and artifacts that
   matched) and up to 48 cited `event_ids`; `start_event_id` and `end_event_id` are the first and last cited.
2. **A finding never contains content.** Evidence is an allowlisted set of keys: `rule` (the strongest rule that matched, a constant sentence),
   `rules` (all that matched), `count`, `event_ids`, and for credentials `artifact_ids` and `kinds` (names from the scanner's closed vocabulary of
   high-confidence kinds). Title and message are constants per detector (`findings/security_words.py`); the only variable part of a message is the
   credential kind put into words ("AWS-style access key"), looked up in that closed vocabulary. No path, command, host, table name or secret value
   is interpolated anywhere, and the detectors never copy event text. Tests plant sentinel strings in every channel and fail if one reaches any
   finding field, any API body of any role, a facet or a search response. Because detection runs over the full events whatever role will read the
   finding (D2), a viewer and an owner get byte-identical findings; the evidence link opens the event, where the normal role rules apply.
3. **Rules** (all deterministic, linear time, bounded; `security/rules.py`):
   - `sec.secret_file_read` (high): `file.read`, `file.modified` or `file.created` whose `file.path`, or a shell command that reads (cat, less,
     head, grep, source, `<`, scp, tar, `curl -d @file` ...) a file, with a secret-bearing name, case-insensitive: `.env` and `.env.*`, private keys
     (`id_rsa`, `id_ed25519` ...), `*.pem`, `*.key`, `*.p12`, `*.jks`, `.aws/credentials`, `.kube/config`, `kubeconfig`, `.netrc`, `.pypirc`, `.pgpass`,
     `.git-credentials`, `.docker/config.json`, `credentials.json`, `client_secret*.json`, `service-account*.json`, `secrets.{yml,json,toml,env,..}`,
     files under `.gnupg/`, and a `.npmrc` in a home directory (a project `.npmrc` holds registry settings, not tokens). Template words
     (`example`, `sample`, `template`, `dist`, `default`, `schema`, `pub`) exempt a name (`.env.example`, `id_rsa.pub`); public CA bundles are exempt.
     Listing, testing, staging, deleting or writing to a file (`ls .env`, `git add .env`, `echo x > .env`) is not reading it, and copying a template
     onto it (`cp .env.example .env`) is not either (only the sources of cp, scp and rsync count). Decided from the name only: the file is not read.
   - `sec.credential_in_output` (critical): an event whose server redaction record (`redaction.server_kinds`) names a **high-confidence** kind
     (`scanner.HIGH_CONFIDENCE_KINDS`: provider-shaped keys and tokens, private keys, JWTs), a server redaction record of one of the run's artifacts
     (`artifacts.metadata.redaction.server_kinds`), or an SDK marker `[REDACTED:<kind>]` of such a kind in an attribute or an inline payload (plain
     string search, bounded in nodes, depth and characters). Assignment-shaped kinds (`secret_assignment`, `credential`, `bearer_token`, `cookie` ...)
     match ordinary code (`token_count = len(x)`) and are not counted. An artifact no event cites is still reported, without an event link.
   - `sec.destructive_command` (high or critical): the recorded `shell.risk_class` and a bounded server re-check, the stricter wins. R4 (critical) is
     recorded R4, or the re-check finds a recursive delete of `/`, `~`, `$HOME`, `*`, `.`, `..` or a system directory, `--no-preserve-root`, `mkfs`,
     `dd of=/dev/..`, a redirect onto a block device, a fork bomb, `dropdb`, or `DROP DATABASE/SCHEMA` through a database client. R3 (high) is the
     re-check finding `git clean` with force and ignored files (`-fdx`), `chmod/chown -R` on `/`, or `DROP TABLE` through a database client, with or
     without a recorded R3. **A recorded R3 that the re-check does not recognise is not reported**: R3 includes `rm file`, `git push` and
     `python -c "import shutil"`, which would drown the real findings (the precision gate). `rm -rf node_modules`, `git reset --hard` and
     `git clean -fd` are not in the set.
   - `sec.risky_git` (high): force pushes (`--force`, `-f` in any flag cluster, `+refspec`; `--force-with-lease` only to a protected name), pushes to
     or deletes of a protected branch name (`main`, `master`, `production`, `release/*`, also as `src:dst`, `refs/heads/..` or `:name`), `git branch
     -d/-D` of a protected name, and history rewrites (`filter-branch`, `filter-repo`, `reflog expire`, `reset --hard HEAD~n`/`<sha>`, `rebase -i`),
     in a shell command or a `git.push` event (`git.branch`, `git.push_target`). `git push`, `git push origin`, a feature-branch push and
     `git reset --hard` / `origin/main` are not reported.
   - `sec.privileged_db` (high): DDL (`CREATE/ALTER/DROP/TRUNCATE`), or `DELETE`/`UPDATE` with no `WHERE` in the same statement, in a database event
     (`db.operation` verb, a `db.statement`/`db.query.text` attribute, or a payload `statement`/`query`/`sql` string) or passed to a database client
     (`psql`, `mysql`, `sqlite3`, `redis-cli FLUSHALL` ...; including `docker exec db psql` and `echo '..' | psql`). Without a client in the command line
     SQL-looking text (`git commit -m "drop table"`) is ignored.
   - `sec.unexpected_destination` (medium): hosts from `http.url` and from the arguments of network programs (curl, wget, httpie, git remotes,
     ssh, scp, rsync, nc, pip/npm/docker index and registry URLs, `git@host:path`) compared with the project's `destination_allowlist` (`host` or
     `*.suffix` for any subdomain; the apex needs its own entry). `localhost`, `127.0.0.0/8`, `::1`, `0.0.0.0` and `*.localhost` are always allowed;
     environment variables and globs are not hosts. **With no allowlist configured it reports nothing**: there is no honest baseline to guess.
4. **Muting and settings.** A project's `muted_detectors` (ids validated against the registry) removes that detector from the project's analyses;
   the allowlist feeds the destination detector. Both come from `project_security_settings` (ADR-130), read by the job. A change applies to a run when it
   is next analysed (a new summary, or `rebuild-findings`); nothing re-analyses on its own (KI-133). Rebuild is deterministic: ids are derived from the
   fingerprint, so muting and unmuting returns the same finding id.
5. **Severity scale.** `medium` and `high` join `info`, `warning`, `critical` (info < warning < medium < high < critical). Migration 0091 widens the two
   CHECK constraints. `run_findings.finding_count/max_severity` keep their meaning (reliability findings), so the run list's badge and the web code that
   reads it are unchanged; `security_count` and `security_max_severity` carry the security category beside them.
6. **API** (metadata only; nothing accepts or returns content):
   - `GET /v1/runs/{id}/findings` includes security findings with `category: "security"` (`reliability` otherwise); `RunOut.security_findings`
     `{count, max_severity, state}` for the run-page chip.
   - `GET /v1/security/findings` (`run.read`; all roles that read runs, keys with `runs:read`; BILLING gets 403, a project-bound key sees its project):
     keyset-paged by run start time then id, filters `severity`, `detector`, `project_id`, `run_status`, `since`, `until`, and `facets` for severity,
     detector and project counted under the other filters. Rows carry run id, name and status, project id/slug/name, detector, severity, the
     detector's fixed title and message, `rule`, `count`, evidence event ids and times. Run names are shown to every run reader already.
   - `GET /v1/security/detectors[?project_id=]` (`run.read`): id, title, description, severities, rule version, and with a project whether the detector
     is muted and active (the destination detector is active only with an allowlist).
   - `GET|PUT /v1/projects/{project_id}/security-settings` (`project.write`: owners and admins): capture mode, muted detectors and allowlist; a list
     given replaces the stored one, omitted fields stay. Changes go through `update_settings` (one transaction with the `security.settings.update`
     audit row). Invalid values are `422 SETTINGS_INVALID` with fixed messages that never repeat the value. Unknown or foreign projects are the same 404.
7. **Search stays no oracle.** Findings are not searchable and no facet or filter of `GET /v1/runs` mentions them; `finding:` remains a reserved word
   exactly as before. The only findings queries are the two routes above, whose fields are the constants described in 2.

## What is not detected (KI-132)

Detection is pattern based on what an event states. It does not see: obfuscated or indirect commands (`$(echo cm0= | base64 -d)`, a command assembled
in a script or a Makefile target, `python -c "open('.env')"`, aliases), a read through a program the tokenizer does not know, a `$(...)` inside double
quotes, hosts reached by a library inside a program, or an ssh alias that resolves elsewhere; a file is judged by name, not by whether it holds a
secret; SQL is judged as text (a quoted keyword in a query matches, as in `SELECT 'create table'`). Prompt-injection indicators, unusual tool usage and
excessive permission requests are not in scope (plan non-goals). These are findings about what the agent did, never a block (Phase 14).

## Alternatives considered

- Findings that quote the matched path or host: more useful, but an oracle for withheld content (D2). Rejected.
- Counting a recorded R3 as destructive: matches the classifier but flags everyday `rm`. Rejected for precision; R3 stays visible in the command's own
  event.
- Baselining destinations from history when no allowlist exists: guesses; a wrong guess is a false alarm or a false comfort. Rejected (D3).
- One finding per matching event: unbounded and noisy; one finding per detector per run with a count and bounded evidence matches the Phase 10 bounds.
- A `category` column: derivable from the detector id prefix; no migration of the findings rows.

## Consequences

- Measured (2026-10-13, Apple silicon laptop): the six detectors over a 10,002-event run with 5,000 distinct shell commands take about 115 ms
  (no cache hits) and the whole pipeline (ingest, summary, detection) of that run about 3.3 s in the test harness; adversarial 200 KB inputs to every
  matcher (cut to 16 KiB head and tail first) take at most 2 ms a call. Events are capped at 4,096 characters per attribute and 64 KiB of inline payload, so one event's
  work is small and bounded; matching results are memoised per run by command text.
- The web client types change (`severity` gains two values, `category` is required on findings); see the step report.
