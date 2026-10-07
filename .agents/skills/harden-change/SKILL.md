---
name: harden-change
description: Simplify and harden an already-verified implementation without changing intended behavior.
---

# Harden Change

Only run after behavior has been verified.

Look for: unnecessary abstractions, speculative base classes, duplicated logic, confusing
naming, dead code, oversized functions, weak type boundaries (untyped dicts crossing
module edges), poor error handling (swallowed exceptions, missing typed error category),
resource leaks (threads, sockets, DB connections), unbounded queues/retries, N+1 queries,
architectural drift, accidental coupling between API modules.

Prefer simplification over abstraction. Do not expand feature scope.

After changes: rerun all relevant verification, compare behavior before and after,
inspect the final diff.
