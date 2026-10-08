"""Deterministic Stage A analytics dataset (spec §61.4): runs, spans and cost lines in bulk SQL.

    cd apps/api && uv run python ../scripts/analytics_seed.py --database-url "$URL" \
        --runs-per-day 100000 --days 7

Writes the *derived* tables the analytics read (`runs`, `spans`, `cost_calculations`) directly with
`generate_series` and hashed pseudo-randomness, so the same arguments always produce the same rows (no
RNG state, no clock). Events are not generated: analytics never read them (ADR-041), and the end-to-end
path from events is covered by the integration tests. Only databases named `abb_p7*` are accepted, and
only the generated workspace's rows are replaced.
"""

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime, time as dtime, timedelta

import asyncpg

WORKSPACE_ID = "00000000-0000-4000-8000-0000000000a7"
WORKSPACE_SLUG = "bench"
PROJECTS = 5
TOOLS = 20
MODELS = ("model-x", "model-x-mini-1", "model-y", "model-z", "model-w", "model-v")

# h(i, salt): a deterministic non-negative pseudo-random integer per (row, purpose).
H = "abs(hashtextextended(({expr})::text || '/{salt}', {seed}))"


def h(expr: str, salt: str, seed: int) -> str:
    return H.format(expr=expr, salt=salt, seed=seed)


async def seed(url: str, runs_per_day: int, days: int, end: datetime, seed_value: int) -> None:
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    name = dsn.rsplit("/", 1)[-1]
    if not name.startswith("abb_p7"):
        sys.exit(f"refusing to seed database {name!r}: only abb_p7* databases are accepted")
    conn = await asyncpg.connect(dsn)
    started = time.monotonic()
    try:
        for rollup in ("runs", "cost", "spans", "latency"):
            await conn.execute(f"DELETE FROM analytics_{rollup}_daily WHERE workspace_id = $1", WORKSPACE_ID)
        await conn.execute("DELETE FROM analytics_top_runs WHERE workspace_id = $1", WORKSPACE_ID)
        await conn.execute("DELETE FROM cost_calculations WHERE workspace_id = $1", WORKSPACE_ID)
        await conn.execute("DELETE FROM spans WHERE workspace_id = $1", WORKSPACE_ID)
        await conn.execute("DELETE FROM runs WHERE workspace_id = $1", WORKSPACE_ID)
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Benchmark', $2) ON CONFLICT DO NOTHING",
            WORKSPACE_ID,
            WORKSPACE_SLUG,
        )
        for p in range(PROJECTS):
            await conn.execute(
                "INSERT INTO projects (workspace_id, id, name, slug) VALUES ($1, md5($2)::uuid, $3, $3) "
                "ON CONFLICT DO NOTHING",
                WORKSPACE_ID,
                f"project/{p}",
                f"p{p}",
            )
        for d in range(days):
            day_start = end - timedelta(days=days - d)
            base = d * runs_per_day
            await one_day(conn, day_start, base, runs_per_day, seed_value, last=d == days - 1)
            print(f"day {d + 1}/{days} done at {time.monotonic() - started:.0f}s", flush=True)
        # Run totals are derived from the lines, exactly as the summarizer does it.
        await conn.execute(
            """
            UPDATE runs r SET summary = r.summary || jsonb_build_object(
                'estimated_cost_usd', c.total, 'retry_cost_usd', coalesce(c.retry, 0),
                'initial_cost_usd', c.total - coalesce(c.retry, 0))
            FROM (SELECT run_id, sum(total) AS total,
                         sum(total) FILTER (WHERE is_retry) AS retry
                  FROM cost_calculations WHERE workspace_id = $1 GROUP BY run_id) c
            WHERE r.workspace_id = $1 AND r.id = c.run_id
            """,
            WORKSPACE_ID,
        )
        await conn.execute(
            """
            UPDATE runs r SET summary = r.summary || jsonb_build_object(
                'tool_spans_finished', c.tool_n, 'tool_spans_ok', c.tool_ok,
                'llm_spans_finished', c.llm_n, 'llm_spans_ok', c.llm_ok,
                'unpriced_calls', coalesce(u.unpriced, 0))
            FROM (SELECT run_id,
                         count(*) FILTER (WHERE kind = 'tool') AS tool_n,
                         count(*) FILTER (WHERE kind = 'tool' AND status = 'success') AS tool_ok,
                         count(*) FILTER (WHERE kind = 'llm') AS llm_n,
                         count(*) FILTER (WHERE kind = 'llm' AND status = 'success') AS llm_ok
                  FROM spans WHERE workspace_id = $1 GROUP BY run_id) c
            LEFT JOIN (SELECT run_id, count(*) AS unpriced FROM cost_calculations
                       WHERE workspace_id = $1 AND source = 'unpriced' GROUP BY run_id) u
                   ON u.run_id = c.run_id
            WHERE r.workspace_id = $1 AND r.id = c.run_id
            """,
            WORKSPACE_ID,
        )
        # The typed copies of the summary figures (what the analytics aggregate), as the summarizer writes them.
        await conn.execute(
            """
            UPDATE runs SET
                cost_usd = (summary->>'estimated_cost_usd')::numeric,
                retry_cost_usd = (summary->>'retry_cost_usd')::numeric,
                llm_calls = (summary->>'llm_calls')::int, tool_calls = (summary->>'tool_calls')::int,
                retry_count = (summary->>'retry_count')::int,
                retries_unattributed = (summary->>'retries_unattributed')::int,
                files_modified = (summary->>'files_modified')::int,
                unpriced_calls = coalesce((summary->>'unpriced_calls')::int, 0),
                tool_spans_finished = coalesce((summary->>'tool_spans_finished')::int, 0),
                tool_spans_ok = coalesce((summary->>'tool_spans_ok')::int, 0),
                llm_spans_finished = coalesce((summary->>'llm_spans_finished')::int, 0),
                llm_spans_ok = coalesce((summary->>'llm_spans_ok')::int, 0)
            WHERE workspace_id = $1
            """,
            WORKSPACE_ID,
        )
        await conn.execute("ANALYZE runs; ANALYZE spans; ANALYZE cost_calculations;")
    finally:
        await conn.close()
    print(f"seeded {runs_per_day * days} runs in {time.monotonic() - started:.0f}s")


async def one_day(
    conn: asyncpg.Connection, day_start: datetime, base: int, count: int, seed_value: int, *, last: bool
) -> None:
    i = "g.i"  # global run index
    status_roll = h(i, "status", seed_value) + " % 100"
    await conn.execute(
        f"""
        INSERT INTO runs (workspace_id, id, project_id, agent_slug, trace_id, name, status, ordering_mode,
                          started_at, completed_at, duration_ms, summary, summary_version, metadata)
        SELECT $1, md5('run/' || {i})::uuid, md5('project/' || ({h(i, "p", seed_value)} % {PROJECTS}))::uuid,
               'agent-' || ({h(i, "agent", seed_value)} % 12), md5('trace/' || {i})::uuid,
               'bench run ' || {i}, s.status, 'sequence', s.started, s.started + s.dur * interval '1 millisecond',
               s.dur, jsonb_build_object(
                 'event_count', 10 + {h(i, "ev", seed_value)} % 90,
                 'llm_calls', s.llm, 'tool_calls', s.tools,
                 'retry_count', s.retries, 'retries_unattributed', 0,
                 'files_modified', {h(i, "files", seed_value)} % 6,
                 'estimated_cost_usd', 0, 'retry_cost_usd', 0),
               2, '{{}}'::jsonb
        FROM generate_series($2::bigint, $3::bigint) AS g(i),
        LATERAL (SELECT
            CASE WHEN {status_roll} < 70 THEN 'SUCCESS' WHEN {status_roll} < 85 THEN 'FAILED'
                 WHEN {status_roll} < 92 THEN 'TIMED_OUT' WHEN {status_roll} < 95 THEN 'BLOCKED'
                 WHEN {status_roll} < 98 OR NOT $5 THEN 'CANCELLED' ELSE 'RUNNING' END AS status,
            $4::timestamptz + ({h(i, "t", seed_value)} % 86400) * interval '1 second' AS started,
            (1000 + {h(i, "dur", seed_value)} % 120000)::double precision AS dur,
            1 + {h(i, "llm", seed_value)} % 5 AS llm,
            {h(i, "tools", seed_value)} % 12 AS tools,
            CASE WHEN {h(i, "retry", seed_value)} % 10 = 0 THEN 1 + {h(i, "rn", seed_value)} % 3 ELSE 0 END AS retries
        ) s
        """,
        WORKSPACE_ID, base, base + count - 1, day_start, last,
    )  # fmt: skip
    # One cost line per model call. Calls after the first are retry cost for runs that retried.
    await conn.execute(
        f"""
        INSERT INTO cost_calculations (workspace_id, event_id, run_id, project_id, agent_slug, span_id,
            occurred_at, run_started_at, provider, model, input_tokens, output_tokens, cached_input_tokens,
            source, pricing_version, pricing_origin, total, is_retry)
        SELECT r.workspace_id, md5('call/' || r.id || '/' || k.k)::uuid, r.id, r.project_id, r.agent_slug,
               md5('callspan/' || r.id || '/' || k.k)::uuid, r.started_at + k.k * interval '1 second',
               r.started_at, 'example-provider', m.model, m.tin, m.tout, 0,
               CASE WHEN m.roll < 80 THEN 'estimated' WHEN m.roll < 90 THEN 'client_estimate'
                    WHEN m.roll < 95 THEN 'provider_reported' ELSE 'unpriced' END,
               '2026-10-01', 'builtin',
               CASE WHEN m.roll >= 95 THEN 0 ELSE round((m.tin * 3.0 + m.tout * 15.0) / 1000000.0, 9) END,
               (r.summary->>'retry_count')::int > 0 AND k.k > 1
        FROM runs r,
        LATERAL generate_series(1, (r.summary->>'llm_calls')::int) AS k(k),
        LATERAL (SELECT (ARRAY{list(MODELS)!r})[1 + {h("r.id::text || k.k", "model", seed_value)} % {len(MODELS)}] AS model,
                 (500 + {h("r.id::text || k.k", "tin", seed_value)} % 20000) AS tin,
                 (50 + {h("r.id::text || k.k", "tout", seed_value)} % 3000) AS tout,
                 {h("r.id::text || k.k", "roll", seed_value)} % 100 AS roll) m
        WHERE r.workspace_id = $1 AND r.started_at >= $2 AND r.started_at < $2 + interval '1 day'
        """,
        WORKSPACE_ID, day_start,
    )  # fmt: skip
    # Spans: llm spans mirror the cost lines, tool spans carry the tool calls.
    await conn.execute(
        f"""
        INSERT INTO spans (workspace_id, id, run_id, trace_id, name, kind, agent_slug, status, started_at,
                           ended_at, duration_ms, event_count, project_id, run_started_at)
        SELECT r.workspace_id, md5('span/' || r.id || '/' || s.k)::uuid, r.id, r.trace_id,
               CASE WHEN s.k <= (r.summary->>'llm_calls')::int THEN 'example-provider/model-x'
                    ELSE 'tool-' || ({h("r.id::text || s.k", "tool", seed_value)} % {TOOLS}) END,
               CASE WHEN s.k <= (r.summary->>'llm_calls')::int THEN 'llm' ELSE 'tool' END,
               r.agent_slug,
               CASE WHEN {h("r.id::text || s.k", "ok", seed_value)} % 100 < 93 THEN 'success' ELSE 'error' END,
               r.started_at + s.k * interval '1 second',
               r.started_at + s.k * interval '1 second' + d.dur * interval '1 millisecond', d.dur, 2,
               r.project_id, r.started_at
        FROM runs r,
        LATERAL generate_series(1, (r.summary->>'llm_calls')::int + (r.summary->>'tool_calls')::int) AS s(k),
        LATERAL (SELECT (20 + {h("r.id::text || s.k", "sd", seed_value)} % 4000)::double precision AS dur) d
        WHERE r.workspace_id = $1 AND r.started_at >= $2 AND r.started_at < $2 + interval '1 day'
        """,
        WORKSPACE_ID, day_start,
    )  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--runs-per-day", type=int, default=100_000)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--end",
        help="exclusive end of the data (default: the next UTC midnight, so the last seeded day is today)",
    )
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if args.end:
        end = datetime.fromisoformat(args.end).astimezone(UTC)
    else:
        end = datetime.combine(datetime.now(UTC).date() + timedelta(days=1), dtime.min, tzinfo=UTC)
    asyncio.run(seed(args.database_url, args.runs_per_day, args.days, end, args.seed))


if __name__ == "__main__":
    main()
