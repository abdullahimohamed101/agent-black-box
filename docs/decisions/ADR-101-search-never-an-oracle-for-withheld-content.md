# ADR-101: Search Never Reveals Withheld Content: Permission-Split Documents, a Compile-Time Gate, No Counts, Flags or Ranking

Status: Accepted (Phase 16; the independent security review is pending). Amends ADR-061.
Date: 2026-10-11
Phase: 16

## Context
ADR-061 made command text and file paths captured content, withheld at read time from actors without `payload.read`. A search
feature can leak the same content by match/no-match, by counts, by ordering, by derived flags (how much content there was), by error differences
and by timing: a VIEWER asking `command:"Bearer s3cr3t"` learns whether a secret exists in any run. Spec §77.1 requires permission-safe search;
§93 classifies paths and commands as Class 2.

## Decision
(1) The rule: an actor may search a field, or receive a derived flag or count, only if the same actor receives that field's values
on the existing read routes. (2) The search document is split into a metadata document and a content document (and content arrays), each with
its own truncation flag, in separate columns; the catalogue marks `file` and `command` as content fields. (3) The compiler takes the `Principal`
and reports content fields before building SQL; the service refuses them through the one `authorize_audited(request, principal, payload.read)`
path (denial logged and audited like every other), with `details.field` = the canonical field name, independent of data; free text compiles to
the metadata document only unless `payload.read` is held; a statement for an actor without `payload.read` never references a content column or
the content truncation flag (asserted on the compiled SQL by a test). (4) No total count, no relevance ranking; facets only over metadata fields
and only for the actor's own compiled predicate; `content_truncated` is returned only with `payload.read`; the unindexed count is over the
run-row scope. (5) Error positions and bodies depend only on the query text, never on data. (6) Keys with `runs:read` hold `payload.read`
(ADR-061) and search content. (7) Indexed metadata is the `span.name` **attribute**, never the derived `Span.name` (which falls back to commands
and paths); unregistered attributes that are not content by name are not indexed at all (fail closed; KI-078's heuristic boundary is inherited,
not widened). (8) Search terms are the searcher's own input: they may sit in their browser history or a reverse proxy's log; the API logs no
query text and the web app sends `Referrer-Policy: no-referrer`. (9) Tests: identical items, facets and cursor for a viewer between a content
canary and a nonsense term in every syntactic form; a statement counter proving no run query runs for a refused field; canary scans of facets,
`matched` and flags; mutation checks that removing any of (3), (4) or (7) fails a test.

## Alternatives
Filtering results after a content match (the count and the emptiness still leak). Returning `[withheld]` matches (tells the
viewer a secret exists). Hashing content terms (equality on a hash is still an oracle). One truncation flag (a volume bit about content).
Searching content for everyone and redacting later (Phase 13's redaction does not exist yet, and a redacted document is still an oracle for the
unredacted part).

## Consequences
A VIEWER's search is strictly weaker than an OWNER's and the UI says so (the help lists only allowed fields; a refused field
names the permission, not the value). Server-side redaction (Phase 13) can later move individual attributes between the two documents by
changing the classification in one place (`abb_api/content.py`). A future ranking feature must rank on metadata only or be gated by
`payload.read` as a whole. Every denial of a content field is visible in the audit log like any other denial.

## As built
- The gate runs after parsing (so a malformed `q` is `422` for a permitted actor) and before the cursor is decoded, a connection is taken or a search permit is acquired:
  a refused field executes no statement that mentions `runs` or `run_search` (asserted with a statement listener). `details.field` is the first content field in query order.
- `matched` omits the `content_truncated` key (not null) for an actor without `payload.read`; `query.fields` lists only the fields the caller may search, and the web help renders
  that list, so a viewer is never offered `file:` or `command:` (verified in the browser by `apps/web/e2e/search.spec.ts`).
- The oracle tests compare `items`, `facets` and `next_cursor` of a content canary against a nonsense term in every syntactic form (free text, prefix, phrase, negated, NFKC confusable).
