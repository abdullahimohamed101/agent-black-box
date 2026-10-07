# ADR-002: PostgreSQL as the MVP System of Record (SQLAlchemy Core, structural tenancy)

Status: Accepted
Date: 2026-10-07

## Context

Spec §57.4: PostgreSQL holds transactional entities and, initially, raw events too, because one database is
cheaper to run and reason about than several. Events are append-heavy and will not stay in a transactional
store forever, so storage must sit behind contracts (INV-7). Tenancy is the highest-consequence invariant (INV-3).

## Decision

- **One PostgreSQL 16 database** for tenancy, runs, spans, events, outbox and API keys. No Redis, Kafka or
  ClickHouse until their measured triggers (spec §57.3-57.5; see ADR-008/009 when written).
- **SQLAlchemy 2 Core, not the ORM.** Tables are `Table` objects in one `MetaData` (`db/tables.py`); repositories
  issue explicit statements and return domain objects. ORM entities therefore cannot leak as API contracts, and
  `INSERT ... ON CONFLICT`, `FOR UPDATE SKIP LOCKED` and keyset queries are written plainly. Alembic migrations are
  hand-reviewed snapshots; a test fails if `tables.py` and the migrations disagree.
- **Tenancy is structural.** Every tenant table is keyed `(workspace_id, id)`; children use composite foreign keys
  `(workspace_id, parent_id)`, so a row cannot reference another tenant's parent even if application code is wrong.
  Repositories are constructed with a `TenantContext` and add the workspace to every statement; no method accepts a
  workspace argument. Another tenant's id is "not found", never "forbidden". The only unscoped paths are explicit and
  small: API key lookup by public key id, workspace provisioning, and the job queue consumer.
- **Public ids are prefixed ULIDs; the database stores their 128 bits as `uuid`** (lossless conversion).
- **Events are append-only**: the application issues only INSERT and SELECT against `events` (a test scans the source),
  and an absent optional JSON value is SQL NULL (`JSONB(none_as_null=True)`).
- **Raw JSON is not retained**: only validated, normalised fields and the schema version are stored. Unknown top-level
  fields from newer producers are dropped; unknown attributes are preserved. Revisit if an adapter needs lossless
  reprocessing.
- **No table partitioning yet** (spec §73.3); monthly partitioning of `events` is an additive migration when volume
  or retention cost demands it.

## Alternatives

- ORM models: faster to write, but couples the API to table shape and hides the SQL that matters for idempotency and locking.
- Row-level security as the primary isolation: kept as optional defence in depth (spec §73.4); it cannot replace tests
  and composite keys, and complicates pooling.
- A single-column primary key per table: simpler, but permits cross-tenant references and id probing.

## Consequences

Positive: tenant isolation is enforced by the database as well as the code; contracts are explicit; operations are boring.
Negative: more repetitive repository code; composite keys make every join wider; analytics over `events` will eventually
outgrow this store (the `EventStore` and `AnalyticsStore` seams exist for that).

## Migration implications

Moving events to a columnar store later changes `PgEventStore` and the read queries, not the SDK contract or the schema.
Migrations are additive and linear; destructive changes follow expand/migrate/contract (spec §110).
