---
name: add-event-type
description: Checklist for adding or changing a canonical event type or attribute, which touches the schema, SDK, API, UI and fixtures.
---

# Add or Change an Event Type

The event contract is the most expensive thing to change (spec §64). Follow in order:

1. Name it dot-delimited, nouns + lifecycle verbs (`file.modified`, `tool.call.completed`).
2. Decide attributes vs payload: small searchable metadata is an attribute (namespaced,
   e.g. `shell.exit_code`); large/sensitive/unbounded data is a payload/artifact reference.
3. Backward compatible = new optional field or new event type. Anything else (rename,
   meaning change, removal) needs a deprecation window or a major schema version and an ADR.
4. Update `packages/event-schema` (JSON Schema + generated/typed models) and its examples.
5. Add valid + invalid fixtures; run the schema compatibility check against the previous
   minor version.
6. SDK: builder/helper, redaction defaults for the new attributes, payload-mode behavior.
7. API: validation passes unknown optional attributes through; add the type to the
   event-class mapping used by timelines and summaries; processors that count it
   (summarizer, cost, findings) updated and idempotent.
8. UI: icon/label/colour grammar, drawer, filter class; handle unknown types gracefully.
9. Fixtures for demo/E2E; golden canonical-event fixture for adapters if applicable.
10. Docs: `docs/architecture/events.md`; SDK/schema compatibility matrix if the version changes.
