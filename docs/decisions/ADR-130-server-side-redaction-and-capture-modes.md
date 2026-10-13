# ADR-130: Server-Side Redaction Before Storage, and Per-Project Capture Modes

Status: Accepted (Phase 13, step 1). Refines the Phase 13 plan's wording of D4 ("DISABLED accepts no events") and amends ADR-031
(the SDK is no longer the only redaction layer; KI-032 and KI-042 are narrowed, not closed).
Date: 2026-10-13
Phase: 13

## Context

Redaction was only the SDK's job (ADR-031): best-effort patterns plus masking of known secret values. A client that skips redaction (another
language, a custom integration, an SDK bug) stored whatever it sent, and artifacts (terminal output, diffs) were never scanned on the server
(KI-032, KI-042). Accepted events are immutable (INV-1), so a secret that is stored cannot be taken back; the only moment the server can keep it
out is before the event is accepted. INV-5 says sensitive capture is opt-in and redaction happens before export.

## Decision

1. **Redaction happens before persistence, deterministically.** `IngestionService` validates an event, reads the project's capture mode, and runs
   `ServerRedactor.apply(event, mode)` *before* `PgEventStore.ingest`. The stored event, its `content_hash`, and the duplicate/conflict decision
   are all computed on the redacted event. Redaction is a pure function of the event and the mode, so redelivery of the same event yields the
   same stored form (duplicate, not conflict). Out-of-order delivery is unaffected. An event the server did not change is returned as the same
   object, so a clean event costs only the scan.
2. **Fail closed.** If a string cannot be scanned (scanner error, over the size budget, over the time budget) it is *dropped*, never stored
   unscanned; the event is kept with its metadata and says why in `redaction.dropped_reason` (`scan_failed` or `too_large`). A dropped attribute
   that the event type requires (`shell.command`, `file.path`) is replaced by the marker `[dropped]` so the stored event still validates and can be
   read back. If the project's settings cannot be read, the batch is refused with a retryable 503 (nothing is stored). If redaction crashes on an
   event, that event is rejected (`REDACTION_FAILED`); an unredacted event never reaches storage.
3. **Three capture modes with the SDK's names and meanings** (`PayloadMode`): `FULL` (payloads and artifacts stored after redaction),
   `METADATA_ONLY` (the default for a project with no settings row), `DISABLED` (= `METADATA_ONLY` and no free text: `error.message` and
   `metadata.*` are dropped, exactly what the SDK's DISABLED drops). This refines the grill wording "DISABLED accepts no events": keeping the
   SDK's meaning means the two layers never disagree about what a mode is; stopping a project from ingesting at all is a separate, existing
   control (revoking its API keys). A test runs the SDK's `Redactor` and the server's for each mode and compares what survives.
4. **What "payload" means.** METADATA_ONLY and DISABLED drop `payload`, `payload_ref` and the artifact-reference attributes
   (`registry.PAYLOAD_REFERENCE_ATTRIBUTES`: `diff.artifact`, `shell.stdout_artifact`, `shell.stderr_artifact`). They do **not** drop the registry's
   *content* attributes (`CONTENT_ATTRIBUTES`: `shell.command`, `file.path` ...). The plan text suggested using `CONTENT_ATTRIBUTES`, but those
   are metadata in the SDK's sense (METADATA_ONLY keeps "attributes and timings"), event types *require* them (`shell.command.*` requires
   `shell.command`, `file.*` requires `file.path`, so dropping them would invalidate the event), the run list and span names are built from
   them, and the Phase 13 detectors (secret-file read, destructive command, risky git) read them. They stay confidential through read-time
   withholding (ADR-061) and are still scanned for secrets in every mode.
5. **What is scanned.** In every mode, every string attribute (and string-list item) and every tag is scanned; in FULL the inline payload too
   (string values and object keys; a string, object or list under a key that contains `password`, `token`, `secret`, `api_key`, `authorization`,
   `cookie`, `credential`, `private_key`, `access_key` is replaced whole, like the SDK's deny-key rule; numbers and booleans are kept). Scanning
   attributes in METADATA_ONLY and DISABLED is an extension beyond the SDK, which redacts attributes in all modes too, so the layers still agree.
   Metadata is scanned first, so when the per-event budget runs out the payload (the bulk) is what is dropped.
6. **Markers.** Registered as metadata in the attribute catalogue (never content, identical for every role): `redaction.server_count` (secret-shaped
   values found, also those inside a payload that was then dropped), `redaction.server_kinds` (sorted kind names, never values) and
   `redaction.dropped_reason` (`mode_metadata_only`, `mode_disabled`, `scan_failed`, `too_large`). A reason is written only when something was
   actually dropped. When several apply, the most informative wins: `scan_failed` > `too_large` > `mode_disabled` > `mode_metadata_only`.
   Client-supplied `redaction.*` attributes are discarded, so the markers always describe what this server did. Text the SDK already
   redacted (`[REDACTED:...]`) is not a finding again: the scanner is idempotent and counts only replacements that change the text.
7. **Artifacts.** The upload path applies the same modes. In METADATA_ONLY and DISABLED the artifact record is created (so events, retries and
   readers keep working and the SDK is not told to retry) but its stored body is a one-line note naming the reason; in FULL the body is scanned
   and redacted before it is hashed and written. A body over the scan budget (`ABB_SECURITY_SCAN_ARTIFACT_MAX_BYTES`, default 4 MiB), a line that
   cannot be split safely, a scanner error or a timeout (`..._MAX_SECONDS`, default 10) also becomes the note (`too_large`, `scan_failed`). The
   outcome (counts and kind names, or the reason) is kept in the artifact row's `metadata.redaction`, so later phases can read it without the
   content. Alternatives considered: refusing with a 4xx (an SDK that does not know the project's mode would see an error for every output, and
   the events that reference the artifact would dangle with no explanation); accepting an empty body (a reader could not tell "empty" from
   "not stored"). `X-Content-SHA256` is verified against the body *as sent*; the returned `sha256` and `size_bytes` describe what is stored. A
   retry is a duplicate if the id exists in the same project and the sent body matches the stored one directly or after FULL-mode redaction
   (independent of the project's mode at retry time); a retry of a dropped body is a duplicate by id (nothing was stored to compare). The body
   is read once, as before (bounded by `ABB_ARTIFACT_MAX_BYTES`); it is scanned in windows and decoded with `surrogateescape` so binary or
   invalid UTF-8 bytes round-trip unchanged.
8. **Mode changes.** The first accepted version of an event wins (INV-1). Redelivering an event after the project's mode changed produces a
   different redacted form, so it is reported as a `conflict` (kept first, nothing overwritten), exactly like any same-id different-content
   delivery. The mode is read for every batch and never cached, so tightening a project takes effect on the next batch.
9. **Per-project settings** live in `project_security_settings` (workspace_id, project_id, capture_mode, muted_detectors, destination_allowlist,
   updated_at, updated_by) behind `SecuritySettingsStore` (INV-7), keyed by workspace and project (INV-3, composite foreign key). No row means
   the defaults (METADATA_ONLY, nothing muted, no allowlist). The muted-detector and allowlist columns exist now because step 2 uses them.
   `update_settings` changes the row and appends the audit entry (`security.settings.update`, with the fields changed, the old and new mode, and
   the first four entries added or removed plus counts) in one transaction: a change cannot exist without its audit row, and a request that
   changes nothing writes neither. **Behaviour change:** a project with no row is METADATA_ONLY, so an existing deployment's projects stop
   storing payloads until set to FULL (`python -m abb_api.cli set-capture-mode`; `make seed` sets the dev project to FULL; the e2e scripts create
   projects with `--capture-mode full`). This is the deliberate secure default.
10. **The scanner** (`abb_api/security/scanner.py`): the SDK's kinds, with patterns rewritten for hostile input. Every quantifier that can be
    followed by a failing token is bounded; whitespace before a value is possessive so the "already redacted" guard cannot be defeated by
    giving a space back; text is cut into windows of at most 64 KiB at newlines (else at a separator; a window with neither raises
    `ScanTooLarge`, because a blind cut could split a secret); a private key is found with plain string search across lines (an unterminated key
    is redacted to the end); NUL and terminal escape sequences are removed first (they can split a secret; the SDK does the same); a per-scan
    byte budget and a time budget raise `ScanTooLarge` / `ScanFailed`; an unexpected exception becomes `ScanFailed` carrying only its type. It
    returns `(text, count, kinds)` and never logs or returns a value. `sk-` keys must not be preceded by an alphanumeric (the SDK's pattern
    flags `risk-assessment-and-task-list`).
11. **Shared code.** The SDK is standard-library only (ADR-013) and does not depend on `abb-event-schema` at runtime, so a shared module in
    `packages/event-schema` would either add a dependency to the SDK or make the SDK import a package that pulls in pydantic. The server
    therefore keeps its own documented copy of the patterns (`apps/api/src/abb_api/security/scanner.py`), and
    `tests/test_security_scanner_parity.py` loads the SDK from the monorepo and asserts the same set of kinds, the same deny keys, the same
    output on a corpus, and the same per-mode decisions.

## What this does not do

- No retroactive scrubbing. Events and artifacts already stored stay as they are (INV-1); the docs say so.
- Detection is best effort, as in the SDK (KI-032): a secret of unknown shape that no pattern recognises is stored in FULL mode. KI-032 and
  KI-042 stay open until the Phase 13 backstop gate (a run with an SDK that skips redaction, scanned end to end) passes in step 4.
- Assignment-shaped kinds (`secret_assignment`, `credential` ...) also match ordinary code such as `token_count = len(x)`; the provider-shaped
  kinds are high confidence. `scanner.HIGH_CONFIDENCE_KINDS` names the latter for the detectors of step 2.
- Findings, the API and the UI for settings arrive in steps 2 and 3.

## Consequences

- One extra indexed read per batch and a scan of the strings an event carries. Measured (2026-10-13, Apple silicon laptop with a 15-minute test
  suite running alongside): the scanner takes about 20 ms/MB on prose, 90-110 ms/MB on logs and diffs, 80 ms/MB when secret-dense, and about
  48 microseconds for a typical 1 KB event; a 100-event batch of such events took a median 43 ms with the scan against 37 ms with the scan bypassed
  (the settings read, 1.5 ms pooled, is in both). Hostile 1 MB inputs (every pattern's worst case) finish in under 0.4 s each.
- Mode defaults change what existing projects store (see 9).
- `ArtifactRecord` gains `redaction`, kept in the existing `artifacts.metadata` column (no artifacts migration).

## Amendment 1 (Phase 13, step 4): the backstop gate

`scripts/security-e2e.sh` ran gate 2 end to end and it passed, after one fix it forced: an artifact's **name** (a query parameter, up to 128
characters) was stored as sent, so an upload with an AWS-style key in its name put the key in `artifacts.name`. `ArtifactService.put` now passes the
name through `ServerRedactor.label` (the same scanner, a small budget; a name that cannot be scanned is stored as no name). Everything else the gate
sent was already redacted or dropped: a command, an inline payload (including a value under a denied key), an error message, an artifact body, an
oversized payload (`too_large`), and, on an API with a nanosecond scan budget and a 2 KiB artifact cap, every scan failure (`scan_failed`, `too_large`),
with nothing unscanned stored. The scan covered 141 text, jsonb, tsvector and bytea columns of the whole database, the artifact directory and the server
logs, with a positive control. KI-032 and KI-042 are resolved; the residue (a secret of unknown shape in FULL mode) is inherent to pattern detection.

Two facts the gate also established, kept as consequences: in METADATA_ONLY and DISABLED the payload is dropped **before it is read**, so a secret inside
it is neither stored nor counted (`redaction.server_count` is absent), and the credential finding needs the server to have seen the value (FULL) or an SDK
marker in an attribute; and a credential an SDK redacted into an artifact leaves only a marker there (KI-134).
