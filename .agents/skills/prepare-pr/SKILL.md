---
name: prepare-pr
description: Prepare the current verified phase branch for pull request submission. Does not push without approval.
disable-model-invocation: true
---

# Prepare Pull Request

Do not push or create a PR without explicit user approval.

Before preparing: confirm working tree state, tests and validation evidence, read the
complete diff, identify breaking changes (event schema / HTTP API / SDK), migrations
(and their rollback story), and unresolved risks.

Produce: Summary, Why, Implementation, Testing (commands + results), Screenshots /
Evidence, Risks, Rollback, Follow-up Work. Link the plan and ADRs.
