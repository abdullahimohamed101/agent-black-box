# Runbook: ingestion rejection spike

**Symptom**: SDK users report dropped events, or a rise in 4xx on `/v1/events*`.

1. Identify the status codes (request log line: `path`, `status`, `workspace_id`, `key_id`). Then:
   - `401 API_KEY_INVALID` (spike for one `key_id`): key revoked or expired, or a client sending a mangled key. The log
     line `api key rejected` carries the real reason (`reason`: `unknown_key`, `bad_secret`, `revoked`, `expired`,
     `malformed_or_missing`); the client only ever sees the uniform 401. Many different unknown keys from one source is
     probing: rate-limit upstream and consider alerting.
   - `403 PROJECT_KEY_REQUIRED` / `INSUFFICIENT_SCOPE`: wrong key type for the SDK.
   - `413 PAYLOAD_TOO_LARGE`: batches over 5 MiB, or single events over 256 KB; the SDK should send smaller batches and
     keep large content out of inline payloads (artifacts arrive in Phase 6).
   - `429 RATE_LIMITED`: a project exceeds its limit; the SDK honours `Retry-After`. Raise `ABB_RATE_LIMIT_*` only after
     checking that the database and API keep up (see the benchmark note).
   - `400/422` with per-event `errors[]`: client sends invalid events. The response body names the field and rule
     (`issues[].loc`, `.code`) and never echoes values; ask the client to run the schema examples against their output.
     `EVENT_SCHEMA_UNSUPPORTED` means a producer on a newer major version than the server accepts.
   - `503 DEPENDENCY_UNAVAILABLE`: the database; see worker-lag step 4. Clients retry with backoff and buffer.
2. A conflict count (`conflicting duplicate events ignored` warning) means a client reuses an `event_id` for different
   content: a client bug (for example ids derived from a timestamp). The first copy wins; nothing is overwritten.
3. Confirm the fix with `make smoke` (stack) or a single `curl` batch.
