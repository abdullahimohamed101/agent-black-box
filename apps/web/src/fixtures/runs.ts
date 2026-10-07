import { FIXTURE_EPOCH, RunBuilder, type FixtureRun } from "./builder";
import { rng } from "./ids";

const MIN = 60_000;

function llm(
  b: RunBuilder,
  k: number,
  model: string,
  inT: number,
  outT: number,
  usd: number,
  ms: number,
) {
  const span = b.span(k);
  const root = b.span(1);
  b.add("llm.request.started", { "llm.provider": "demo", "llm.model": model }, 300, {
    span_id: span,
    parent_span_id: root,
    payload: { messages: [{ role: "user", content: "Fix the failing login test." }] },
  });
  b.add(
    "llm.request.completed",
    {
      "llm.provider": "demo",
      "llm.model": model,
      "llm.input_tokens": inT,
      "llm.output_tokens": outT,
      "cost.estimated_usd": usd,
    },
    ms,
    {
      span_id: span,
      parent_span_id: root,
      status: "success",
      duration_ms: ms,
      payload: { response: "I will read the auth module, then patch the session expiry check." },
    },
  );
}

function tool(b: RunBuilder, k: number, name: string, ms: number, fail?: string) {
  const span = b.span(k);
  const root = b.span(1);
  b.add("tool.call.started", { "tool.name": name }, 200, { span_id: span, parent_span_id: root });
  if (fail) {
    b.add("tool.call.failed", { "tool.name": name, "tool.error_type": fail }, ms, {
      span_id: span,
      parent_span_id: root,
      status: "error",
      duration_ms: ms,
      payload: { error: { type: fail, message: `${name} failed: ${fail}` } },
    });
  } else {
    b.add("tool.call.completed", { "tool.name": name, "tool.result_count": 3 }, ms, {
      span_id: span,
      parent_span_id: root,
      status: "success",
      duration_ms: ms,
    });
  }
}

function open(b: RunBuilder, runName: string) {
  b.add("run.started", { "run.name": runName }, 0);
  b.add("agent.started", {}, 100, { span_id: b.span(1) });
}

export function successRun(): FixtureRun {
  const b = new RunBuilder(1, "Fix login session expiry", "coding-agent", FIXTURE_EPOCH + 5 * MIN);
  open(b, "Fix login session expiry");
  b.add("file.read", { "file.path": "src/auth/login.ts" });
  b.add("file.read", { "file.path": "src/auth/session.ts" });
  llm(b, 2, "model-x", 1840, 212, 0.0121, 1900);
  tool(b, 3, "search_code", 640);
  b.add("file.modified", {
    "file.path": "src/auth/session.ts",
    "file.lines_added": 6,
    "file.lines_removed": 2,
  });
  b.add("shell.command.started", { "shell.command": "pnpm test auth", "shell.cwd": "/repo" }, 300);
  b.add(
    "shell.command.completed",
    { "shell.command": "pnpm test auth", "shell.exit_code": 0 },
    4200,
    {
      status: "success",
      duration_ms: 4200,
    },
  );
  llm(b, 4, "model-x", 2210, 96, 0.0132, 1100);
  b.add("agent.completed", {}, 200, { span_id: b.span(1), status: "success" });
  b.add("run.completed", {}, 100, { status: "success" });
  return b.build();
}

export function failureRetryRun(): FixtureRun {
  const b = new RunBuilder(2, "Migrate billing schema", "coding-agent", FIXTURE_EPOCH + 30 * MIN);
  open(b, "Migrate billing schema");
  b.add("file.read", { "file.path": "db/migrations/0042_billing.sql" });
  llm(b, 2, "model-x", 2300, 340, 0.0189, 2100);
  tool(b, 3, "run_migration", 3100, "ConnectionTimeout");
  b.add(
    "retry.attempted",
    { "retry.attempt": 1, "retry.reason": "ConnectionTimeout", "retry.delay_ms": 1000 },
    1000,
  );
  tool(b, 4, "run_migration", 2900, "ConnectionTimeout");
  b.add(
    "retry.attempted",
    { "retry.attempt": 2, "retry.reason": "ConnectionTimeout", "retry.delay_ms": 2000 },
    2000,
  );
  llm(b, 5, "model-x", 2600, 120, 0.0167, 1500);
  b.add("agent.failed", { "error.summary": "migration could not connect" }, 200, {
    span_id: b.span(1),
    status: "error",
  });
  b.add("run.failed", {}, 100, { status: "error" });
  return b.build();
}

export function expensiveRun(): FixtureRun {
  const b = new RunBuilder(
    3,
    "Refactor payments module (large context)",
    "refactor-agent",
    FIXTURE_EPOCH + 55 * MIN,
  );
  open(b, "Refactor payments module (large context)");
  const r = rng(7);
  for (let i = 0; i < 28; i++) {
    b.add("file.read", { "file.path": `src/payments/part${i}.ts` }, 150);
    llm(
      b,
      10 + i,
      "model-large",
      38000 + Math.floor(r() * 20000),
      1200 + Math.floor(r() * 900),
      0.55 + r() * 0.4,
      6000 + Math.floor(r() * 4000),
    );
    if (i % 4 === 0) tool(b, 100 + i, "run_tests", 8000);
  }
  b.add("agent.completed", {}, 200, { span_id: b.span(1), status: "success" });
  b.add("run.completed", {}, 100, { status: "success" });
  return b.build();
}

export function runningRun(): FixtureRun {
  const b = new RunBuilder(4, "Triage open issues", "triage-agent", FIXTURE_EPOCH + 80 * MIN);
  open(b, "Triage open issues");
  llm(b, 2, "model-x", 900, 80, 0.006, 800);
  tool(b, 3, "list_issues", 500);
  return b.build();
}

export function approvalRun(): FixtureRun {
  const b = new RunBuilder(5, "Deploy to staging", "deploy-agent", FIXTURE_EPOCH + 90 * MIN);
  open(b, "Deploy to staging");
  tool(b, 2, "plan_deploy", 700);
  b.add("approval.requested", { "approval.action": "deploy staging" }, 300);
  return b.build();
}

/** 10,000 events: the virtualization stress case (§135). */
export function stressRun(): FixtureRun {
  const b = new RunBuilder(
    6,
    "Stress: long-running crawl (10,000 events)",
    "crawler",
    FIXTURE_EPOCH + 100 * MIN,
  );
  open(b, "Stress: long-running crawl (10,000 events)");
  const r = rng(42);
  let k = 10;
  while (b.events.length < 9_996) {
    const p = r();
    if (p < 0.5) b.add("file.read", { "file.path": `data/chunk-${b.events.length}.json` }, 20);
    else if (p < 0.8)
      tool(b, k++, "fetch_page", 30 + Math.floor(r() * 200), r() < 0.02 ? "HttpError" : undefined);
    else if (p < 0.9) llm(b, k++, "model-x", 400, 40, 0.001, 300);
    else b.add("retry.attempted", { "retry.attempt": 1, "retry.reason": "HttpError" }, 10);
  }
  b.events.length = Math.min(b.events.length, 9_998);
  while (b.events.length < 9_998) b.add("file.read", { "file.path": "data/tail.json" }, 20);
  b.add("agent.completed", {}, 100, { span_id: b.span(1), status: "success" });
  b.add("run.completed", {}, 100, { status: "success" });
  return b.build();
}
