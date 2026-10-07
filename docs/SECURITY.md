# Security

Agent Black Box stores source code, prompts, shell output, file names, database query text and
tool arguments. Treat it as a high-value target and all captured telemetry as hostile input.
Source: spec §32, §38, §90-93, §126; threat model to be expanded as phases land.

## Assets and boundaries
Customer prompts/responses, diffs, terminal output, API keys, tool arguments, policy config,
approval authority, trace history, user identity. Boundaries: customer agent process | internet |
ingestion edge | control plane | database/object storage | human approvers.

## Requirements (enforced by tests where stated)
- API keys: `abb_live_<prefix>.<secret>`; store prefix + hash only; secret shown once; scopes
  (`events:write`, `runs:read`, `artifacts:write`, `policy:check`); revocation; `last_used_at`. (Phase 2)
- Tenancy: workspace ID on every record and repository call; cross-workspace tests per
  endpoint (Phases 2, 15); optional Postgres RLS as defence in depth (§73.4).
- Redaction: SDK-side before export, server-side backstop, payload modes FULL/METADATA_ONLY/DISABLED. (Phases 3, 13)
- Never log secrets or payload bodies; request IDs in logs; audit events not sampled.
- Rendering: payloads displayed as text; sanitize any markup; no `dangerouslySetInnerHTML` on trace data.
- Approvals bound to the exact action hash, single use, atomic consume. (Phase 14)
- Rate limits, size limits, bounded queues against telemetry flooding. (Phases 2, 19)
- Dependencies: lockfiles, scheduled vulnerability scans, minimal SDK deps.
- Local/demo credentials are generated, documented as dev-only, never real.

## Process
Security-sensitive changes require a `review-change` pass and a note in the phase plan.
Incidents: severity scale and flow in spec §124; cross-tenant exposure is SEV-1.
