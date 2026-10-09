# ADR-030: Artifacts Live Behind an `ArtifactStore` Contract, Uploaded Client-Id-First and Read in Chunks

Status: Accepted (implemented in Phase 6)
Date: 2026-10-07

## Context
Diffs and terminal output are too large and too sensitive for the hot `events` table (spec §75, INV-5, INV-7). Phase 6 needs a store
(local filesystem now, S3-compatible in Phase 18), an upload path the SDK can use, content hashes (§75.2), and a read path that lets the
browser load a 5 MB log without downloading it. Events are ingested in any order (INV: never trust arrival order), and the SDK must not
block the agent on I/O (INV-4). A skeleton `artifacts` table (migration 0005) already exists, with a foreign key to `runs`.

## Decision
1. **Contract (`abb_api/artifacts/store.py`)**: `ArtifactStore` protocol with `put(key, data)`, `read(key, offset, length)` and `delete(key)`;
   a key is always `<workspace uuid>/<artifact uuid>` built by the service from validated ids, never from client text. `LocalFsArtifactStore`
   (`ABB_ARTIFACT_DIR`, default `.artifacts`) writes atomically (temp file + rename), creates directories `0700` and files `0600`, and runs blocking I/O in a
   worker thread. A remote store replaces it without touching routers or the SDK.
2. **Upload**: `PUT /v1/artifacts/{artifact_id}?run_id=&kind=&name=` with a raw body and scope `artifacts:write` on a project-bound key. The
   **client generates the id** (`art_<ULID>`), so the SDK can reference it in an event at once and retries are idempotent: the same id and
   hash returns `200` with the existing record, a different hash is `409 ARTIFACT_CONFLICT` (artifacts are immutable like events, INV-1).
   An optional `X-Content-SHA256` must match what the server computes (`422 ARTIFACT_HASH_MISMATCH`). Size is capped while streaming
   (`ABB_ARTIFACT_MAX_BYTES`, default 8 MiB, `413`), gzip is accepted with a decompressed cap, uploads count against the project's rate limit,
   and `Content-Type` must be `text/plain`, `application/json` or `application/octet-stream`.
3. **Schema** (migration 0044, additive; numbered 0030 while Phase 7 was in flight, renumbered at merge so the history stays linear after 0043): `project_id` (composite foreign key to `projects`), `name`, `media_type`; `content_hash` holds the SHA-256 digest;
   `artifact_type` holds the kind (`diff|stdout|stderr|file|text|other`). The foreign key to `runs` is **dropped**: an artifact can arrive before the first
   event of its run, and a placeholder run row would corrupt `trace_id`/`agent` (`ON CONFLICT DO NOTHING`). Tenant integrity is kept by `(workspace_id, ...)` keys
   and project scoping on every read. Cost: deleting a run no longer cascades (KI-040, retention is Phase 19).
4. **Read**: `GET /v1/artifacts/{id}` (metadata) and `GET /v1/artifacts/{id}/content?offset=&limit=` (scope `runs:read`, project keys see their project only,
   everything else `404`). The body is JSON `{content, offset, next_offset, total_bytes}`: bytes decoded as UTF-8 (invalid bytes replaced), the
   end aligned back to a character boundary, default chunk 64 KiB, maximum 256 KiB. Content is **never served as a document**, so a stored HTML payload cannot execute.
5. **Events reference artifacts by attribute** (`diff.artifact`, `shell.stdout_artifact`, `shell.stderr_artifact`, value `artifact://<id>`); `payload_ref` keeps its
   generic meaning and is unused by the coding events. Missing artifacts (not uploaded yet, dropped, redacted away) are a normal UI state.
6. **No server-side scanning in Phase 6.** Secrets are removed before upload by the SDK (ADR-031); server detectors are Phase 13 (KI-042). Capture is opt-in
   (`PayloadMode.FULL`); in the default mode the SDK uploads nothing.

## Consequences
- ADR-005 ("large payloads as artifacts") is now implemented by this decision.
- Replicas need a shared directory until the S3 store (KI-041). No quota yet (KI-040).
- The web proxy gains two read paths (`/v1/artifacts/{id}`, `.../content`); it still never forwards writes.
