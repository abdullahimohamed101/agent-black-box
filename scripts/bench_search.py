"""Search latency (plan AC-12, D6 triggers): seeds runs with search documents in bulk SQL, then times `GET /v1/runs?q=`.

    cd apps/api
    uv run python ../../scripts/bench_search.py --database-url "$ABB_DATABASE_URL_OF_A_SCRATCH_DB" \
        --workspace bench --project demo --runs 10000 --seed
    uv run python ../../scripts/bench_search.py --url http://localhost:8211 --key-file KEY --queries 200

The workspace, project and a `runs:read` key are created beforehand with the CLI (`create-workspace`,
`create-project`, `create-key`). The documents are written directly with `to_tsvector` over plain words: that is a
benchmark corpus, not the production writer (`search/document.py`), but the columns, arrays and indexes are the real
ones, so the planner sees the real shapes. Only databases named `abb_p16*` are accepted. Numbers depend on the machine;
they are a regression baseline for this repository, not a capacity claim. Record the environment next to any figure.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import asyncpg
import httpx

TOOLS = 20
MODELS = ("model-x", "model-x-mini-1", "model-y", "model-z", "model-w", "model-v")
EVENT_TYPES = (
    "run.started", "agent.started", "llm.request.started", "llm.request.completed", "tool.call.started",
    "tool.call.completed", "tool.call.failed", "shell.command.started", "shell.command.completed",
    "file.read", "file.modified", "run.completed",
)  # fmt: skip
SEED = 16


def h(expr: str, salt: str) -> str:
    return f"abs(hashtextextended(({expr})::text || '/{salt}', {SEED}))"


async def seed(url: str, workspace: str, project: str, runs: int) -> None:
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    if not dsn.rsplit("/", 1)[-1].startswith("abb_p16"):
        sys.exit("refusing: only abb_p16* databases are accepted")
    conn = await asyncpg.connect(dsn)
    started = time.monotonic()
    try:
        ws = await conn.fetchval("select id from workspaces where slug = $1", workspace)
        pj = await conn.fetchval("select id from projects where workspace_id = $1 and slug = $2", ws, project)
        if ws is None or pj is None:
            sys.exit("create the workspace and project with the CLI first")
        await conn.execute("delete from runs where workspace_id = $1 and name like 'bench search %'", ws)
        i = "g.i"
        await conn.execute(
            f"""
            INSERT INTO runs (workspace_id, id, project_id, agent_slug, trace_id, name, status, ordering_mode,
                              started_at, completed_at, duration_ms, summary, summary_version, metadata,
                              cost_usd, retry_count)
            SELECT $1, md5('sr/' || {i})::uuid, $2, 'agent-' || ({h(i, "agent")} % 12), md5('st/' || {i})::uuid,
                   'bench search ' || {i}, s.status, 'sequence', s.started, s.started + s.dur * interval '1 ms',
                   s.dur, jsonb_build_object('event_count', 10 + {h(i, "ev")} % 90, 'error_count', 0), 4, '{{}}'::jsonb,
                   round(({h(i, "cost")} % 300)::numeric / 100, 4), CASE WHEN {h(i, "r")} % 10 = 0 THEN 2 ELSE 0 END
            FROM generate_series(1, $3::bigint) AS g(i),
            LATERAL (SELECT
                CASE WHEN {h(i, "st")} % 100 < 70 THEN 'SUCCESS' WHEN {h(i, "st")} % 100 < 85 THEN 'FAILED'
                     WHEN {h(i, "st")} % 100 < 92 THEN 'TIMED_OUT' ELSE 'CANCELLED' END AS status,
                now() - ({h(i, "t")} % (90 * 86400)) * interval '1 second' AS started,
                (1000 + {h(i, "d")} % 120000)::double precision AS dur) s
            """,
            ws, pj, runs,
        )  # fmt: skip
        # Documents: arrays of the real shapes, plus text for the two tsvector columns.
        await conn.execute(
            f"""
            INSERT INTO run_search (workspace_id, run_id, project_id, started_at, summary_version, document_hash,
                error_count, security_events, event_types, tools, models, providers, agents, tags, metadata_doc,
                file_paths, commands, content_doc)
            SELECT r.workspace_id, r.id, r.project_id, r.started_at, 4, sha256(convert_to(r.id::text, 'UTF8')),
                   0, 0, d.event_types, d.tools, d.models, ARRAY['demo'], ARRAY[r.agent_slug], ARRAY['bench'],
                   to_tsvector('simple', r.name || ' ' || d.words || ' ' || array_to_string(d.tools, ' ')),
                   d.paths, d.commands,
                   to_tsvector('simple', array_to_string(d.commands, ' ') || ' ' || array_to_string(d.paths, ' '))
            FROM runs r,
            LATERAL (SELECT regexp_replace(r.name, '\\D', '', 'g')::bigint AS n) k,
            LATERAL (SELECT
              ARRAY(SELECT DISTINCT 'tool-' || ({h("r.id::text || x", "tool")} % {TOOLS})
                    FROM generate_series(1, 1 + {h("r.id", "nt")} % 4) x) AS tools,
              ARRAY(SELECT DISTINCT (ARRAY{list(MODELS)!r})[1 + {h("r.id::text || x", "m")} % {len(MODELS)}]
                    FROM generate_series(1, 1 + {h("r.id", "nm")} % 2) x) AS models,
              ARRAY(SELECT DISTINCT (ARRAY{list(EVENT_TYPES)!r})[1 + {h("r.id::text || x", "e")} % {len(EVENT_TYPES)}]
                    FROM generate_series(1, 5 + {h("r.id", "ne")} % 6) x) AS event_types,
              'w' || ({h("r.id", "w1")} % 200) || ' w' || ({h("r.id", "w2")} % 200) || ' w' || ({h("r.id", "w3")} % 200)
                || CASE WHEN {h("r.id", "dep")} % 20 = 0 THEN ' deploy failed' WHEN {h("r.id", "dep")} % 3 = 0 THEN ' deploy' ELSE '' END
                || CASE WHEN k.n % 1000 = 0 THEN ' rareword' ELSE '' END AS words,
              ARRAY(SELECT CASE x % 4
                        WHEN 0 THEN 'npm test -- src/mod' || ({h("r.id::text || x", "c")} % 40) || '/index.test.ts'
                        WHEN 1 THEN 'git push origin feature-' || ({h("r.id::text || x", "c")} % 100)
                        WHEN 2 THEN 'curl -s https://api.example.com/v' || ({h("r.id::text || x", "c")} % 9)
                        ELSE 'pnpm build' END
                    FROM generate_series(1, 2 + {h("r.id", "nc")} % 5) x) AS commands,
              ARRAY(SELECT '/home/dev/proj/src/' || (ARRAY['auth','billing','db','ui','api'])[1 + {h("r.id::text || x", "pm")} % 5]
                        || '/mod' || ({h("r.id::text || x", "pf")} % 40) || (ARRAY['.ts','.tsx','.py','.md'])[1 + {h("r.id::text || x", "px")} % 4]
                    FROM generate_series(1, 3 + {h("r.id", "np")} % 6) x) AS paths) d
            WHERE r.workspace_id = $1 AND r.name LIKE 'bench search %'
            """,
            ws,
        )  # fmt: skip
        await conn.execute("ANALYZE runs; ANALYZE run_search;")
        sizes = await conn.fetch(
            "select relname, pg_relation_size(c.oid) as bytes from pg_class c where relname = 'run_search' "
            "or relname like 'ix_run_search%' or relname = 'run_search_pkey' order by relname"
        )
        total = await conn.fetchval("select pg_total_relation_size('run_search')")
        count = await conn.fetchval("select count(*) from run_search where workspace_id = $1", ws)
        print(f"seeded {runs} runs ({count} documents) in {time.monotonic() - started:.0f}s")
        for row in sizes:
            print(f"  {row['relname']:32} {row['bytes'] / 1024:>10,.0f} KiB")
        print(f"  run_search total (heap + indexes + toast): {total / 1024:,.0f} KiB "
              f"= {total / max(count, 1):,.0f} bytes per run")  # fmt: skip
    finally:
        await conn.close()


def classes(n: int) -> dict[str, list[str]]:
    """The query shapes of plan AC-12, `n` variations each (every one is also answered with facets)."""

    def many(templates: list[str]) -> list[str]:
        return [templates[k % len(templates)].format(k=k % 20, j=k % 40, c=round((k % 25) / 10, 1)) for k in range(n)]

    return {
        "run-row only": many(["status:failed cost:>{c}", "retries:>=2 status:success", "duration:>60s started:>=-7d", "errors:>0"]),
        "array contains": many(["tool:tool-{k}", "model:model-x event:tool.call.failed", "agent:agent-{k} status:failed", "tool:tool-{k} tool:tool-3"]),
        "metadata text": many(["w{k}", '"deploy failed"', "deploy w{k}", "rareword", "dep*"]),
        "content text": many(["mod{j}", "login mod{j}", "feature-{j}", "api.example.com"]),
        "command: substring": many(['command:"npm test"', "command:feature-{j}", "command:curl", "command:index.test"]),
        "file: glob": many(["file:src/auth/**", "file:*.ts", "file:*mod{j}.*", "file:/home/dev/proj/src/db/**"]),
    }  # fmt: skip


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


async def query(url: str, key: str, n: int, out: Path | None) -> None:
    rows = []
    async with httpx.AsyncClient(
        base_url=url, headers={"Authorization": f"Bearer {key}"}, timeout=30
    ) as client:
        for name, qs in classes(n).items():
            ms, bad, hits = [], 0, 0
            for q in qs:
                t0 = time.perf_counter()
                r = await client.get("/v1/runs", params={"q": q, "limit": 50})
                ms.append((time.perf_counter() - t0) * 1000)
                if r.status_code != 200:
                    bad += 1
                else:
                    hits += len(r.json()["items"])
            row = {
                "class": name, "n": len(ms), "p50_ms": round(pct(ms, 0.5), 1), "p95_ms": round(pct(ms, 0.95), 1),
                "p99_ms": round(pct(ms, 0.99), 1), "max_ms": round(max(ms), 1), "mean_ms": round(statistics.fmean(ms), 1),
                "non_200": bad, "mean_rows": round(hits / len(ms), 1),
            }  # fmt: skip
            rows.append(row)
            print(f"{name:22} n={row['n']:<4} p50={row['p50_ms']:>7} p95={row['p95_ms']:>7} p99={row['p99_ms']:>7} "
                  f"max={row['max_ms']:>7} non-200={bad} mean rows={row['mean_rows']}")  # fmt: skip
    if out:
        out.write_text(json.dumps(rows, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", action="store_true", help="seed documents instead of querying")
    p.add_argument("--database-url", help="with --seed: an abb_p16* database")
    p.add_argument("--workspace", default="bench")
    p.add_argument("--project", default="demo")
    p.add_argument("--runs", type=int, default=10_000)
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--key-file", type=Path)
    p.add_argument("--queries", type=int, default=200, help="variations per class")
    p.add_argument("--json", type=Path)
    args = p.parse_args()
    if args.seed:
        if not args.database_url:
            sys.exit("--database-url is required with --seed")
        asyncio.run(seed(args.database_url, args.workspace, args.project, args.runs))
    else:
        if not args.key_file:
            sys.exit("--key-file is required")
        asyncio.run(query(args.url, args.key_file.read_text().strip(), args.queries, args.json))


if __name__ == "__main__":
    main()
