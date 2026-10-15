# ADR-158: Alert destination port allow-list and a fair share of search permits

Status: Accepted (Phase 19a, step 6). Implements two items of D4 and gate 6 of the Phase 19a plan. Resolves KI-162 and KI-082 (the GitHub issues close with the close-out PR). Amends ADR-151 (outbound safety) and the per-process search bounds of ADR-100.
Date: 2026-10-15
Phase: 19a

## Context

Two small gaps remained after the shared limiter (ADR-152) landed.

1. A webhook or Slack destination could name any public host **and any port**, so a person holding an owner session could make the delivery worker send blind HTTPS requests at SSH, SMTP, Redis, Elasticsearch and other services of third parties (KI-162). The address guard (ADR-151) only judges *where* a name points, not *which service* it reaches.
2. Search concurrency (`ABB_SEARCH_MAX_CONCURRENT`, default 4) was one semaphore per process, so one actor could occupy every permit, and the permit was held while the request waited for a pooled connection (`pool_timeout`, 5 s) outside the search budget, so a search could hold a permit for budget plus 5 s and then answer `DEPENDENCY_UNAVAILABLE` instead of `SEARCH_TIMEOUT` (KI-082).

## Decision

### 1. Port allow-list (KI-162)

* **Setting** `ABB_ALERTS_ALLOWED_PORTS`: a comma list of ports, default `443`. Every entry must be an integer in 1-65535 and the list must not be empty; anything else refuses to start (the message names the setting, never a value from elsewhere). It is read by the API and the worker.
* **Effective port.** An `https://` URL without a port means 443. The explicit port, or 443, must be in the list. Port `0` and `65536` are not ports (`port_invalid`, `url_malformed`) and are refused before the allow-list is consulted.
* **Three places, one rule.** (a) **Save and update:** a refused port is the existing `422 DESTINATION_TARGET_INVALID` with the new fixed reason `port_not_allowed`. (b) **Delivery and send-test:** the sender itself (`SafeHttpsSender`) checks the port first, before any lookup or connection, and answers the new fixed code `blocked_port`. This is the defence for a destination saved while a wider list was in force; both paths use the same sender so one check covers them. `blocked_port` is **permanent** (not in `RETRYABLE_CODES`), like `blocked_address`: the alert goes `dead` on the first attempt and the destination's failure counter and auto-pause apply as for any permanent failure. The fix is to move the destination to an allowed port or to have the operator allow it.
* **The sender's default is the safe one.** `SafeHttpsSender` allows `{443}` unless it is constructed with a list; production passes `settings.alerts_allowed_port_set`. Nothing in the sender can be switched off by the caller.
* **Email is unaffected**: it uses the operator's SMTP relay, not a user-chosen host.
* **The test network keeps the guard.** `scripts/alerts-e2e.sh` adds its receiver ports (main, Slack, flaky) through the same setting an operator would use. The test network file (ADR-151 addendum) changes only DNS, the connect address and TLS trust; it does not touch the port check, and `tests/test_alerts_testnet.py` proves it.
* **Residual risk, accepted.** Blind requests to public hosts on 443 remain possible. They are bounded by 20 deliveries a minute and 500 a day per destination, the destination count per workspace, the 5 s deadline, the attempt cap and auto-pause (ADR-151). A response is never shown.

### 2. Search: a fair share of permits (KI-082)

Concurrency is a process-local resource by nature (it protects this process's share of the pool and event loop), so it stays in-process; the request *rate* already uses the shared limiter. Within a process, with `cap` = `ABB_SEARCH_MAX_CONCURRENT`:

* an **actor** (API key or person, `Principal.actor_id`) holds at most `min(2, max(1, cap // 2))` permits;
* a **workspace** holds at most `max(1, cap // 2)` permits while another workspace holds one, and at most `max(that, cap - 1)` while it is the only one searching. A lone user is therefore not held to half the process, but one permit always stays free for the next workspace to arrive. Actor and workspace shares are independent of the number of people: they are fixed fractions, not a division among those currently active, which keeps admission a constant-time check with no state beyond three counters;
* the process holds at most `cap`.

With the default `cap` 4 that is two permits per actor, two per workspace (three when alone) and four in total. With `cap` 1 there is nothing to share and the permit goes to whoever asks first.

A refusal is the existing `503 SEARCH_BUSY` with `Retry-After: 1` and a fixed `details.reason`: `actor_limit`, `workspace_share` or `process_full` (checked in that order, most specific first, so a noisy actor learns it is its own limit). Searches are never queued.

Admission is one synchronous check-and-increment on the event loop, so it needs no lock. The permit is released in a `finally` on every path (error, timeout, cancellation, client disconnect), and the per-workspace and per-actor tables drop idle keys, so a refused or finished search leaves nothing behind (tests assert the count is zero and the tables empty).

**The pool wait is inside the budget.** The budget (and its clock) starts when the permit is granted, and the wait for a pooled connection is now bounded by what remains of it (`SearchBudget.connect`). If the pool is starved when the budget ends the answer is `503 SEARCH_TIMEOUT` within about the budget, and the permit is released then, instead of budget + `pool_timeout` and `DEPENDENCY_UNAVAILABLE`. The permit is still taken before the connection so search cannot hold more than `cap` of the pool.

### Alternatives considered

* **Allow-list of ports per workspace in the database.** More flexible, but a new table and an owner-facing way to widen the egress surface; the operator setting is enough and is the decision taken with the user.
* **Blocking only a deny-list of well-known ports.** Fails open for every service not on the list.
* **Dividing permits equally among the workspaces currently searching.** Fairer in the limit but needs rebalancing on every arrival and departure and can starve a workspace that already holds permits; fixed fractions are simpler to reason about and to test.
* **A shared (database) permit counter across API processes.** A permit that outlives a crashed process would leak; per-process permits die with the process. The shared rate limiter already bounds the rate across processes.

## Consequences

* Existing destinations on a non-443 port keep their row but fail at delivery with `blocked_port` until the operator adds the port; the history shows words for it, and the runbook says what to do.
* Operators who legitimately need other ports (an internal-looking gateway on a public name) set `ABB_ALERTS_ALLOWED_PORTS=443,8443`.
* A flood by one search key now sees `SEARCH_BUSY` after its second concurrent search even when the process is idle; that is intended and the reason says so.
