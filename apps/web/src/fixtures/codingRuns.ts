import { FIXTURE_EPOCH, RunBuilder, type FixtureRun } from "./builder";

/**
 * The two coding-agent demo runs the evaluation, comparison and experiment screens are shown with (Phase 11). They
 * mirror `examples/coding-agent --variant baseline|wrong-fix`: the same first moves, then the baseline patches
 * `app/session.py`, passes and pushes while the wrong fix edits `app/oauth.py`, still fails the tests and ends in error.
 * Builder numbers 11 and 12 (1-10 belong to the earlier fixtures); both start before every other fixture run.
 */
export const BASELINE_RUN_N = 11;
export const WRONG_FIX_RUN_N = 12;
const MIN = 60_000;
const MODEL = { "llm.provider": "scripted", "llm.model": "scripted-agent-1" };
const TEST_COMMAND = "python -m unittest discover -s tests -t .";

function codingRun(n: number, wrong: boolean): FixtureRun {
  const b = new RunBuilder(
    n,
    "Fix OAuth session expiry",
    "coding-agent",
    FIXTURE_EPOCH - (wrong ? 140 : 180) * MIN,
  );
  const root = b.span(1);
  let k = 1;
  const llm = () => {
    const span = b.span(++k);
    b.add("llm.request.started", MODEL, 300, { span_id: span, parent_span_id: root });
    b.add(
      "llm.request.completed",
      {
        ...MODEL,
        "llm.input_tokens": 900 + 650 * k,
        "llm.output_tokens": 110,
        "cost.estimated_usd": 0.0045 + 0.0011 * k,
      },
      700,
      { span_id: span, parent_span_id: root, status: "success", duration_ms: 700 },
    );
  };
  const tool = (name: string, inner: (span: string) => void = () => {}) => {
    const span = b.span(++k);
    b.add("tool.call.started", { "tool.name": name }, 200, { span_id: span, parent_span_id: root });
    inner(span);
    b.add("tool.call.completed", { "tool.name": name }, 300, {
      span_id: span,
      parent_span_id: root,
      status: "success",
      duration_ms: 300,
    });
  };
  const read = (path: string) => (span: string) =>
    b.add(
      "file.read",
      { "file.path": path, "file.operation": "read", "file.language": "python" },
      100,
      { span_id: span, parent_span_id: root },
    );
  const edit = (path: string) => (span: string) => {
    read(path)(span);
    b.add(
      "file.modified",
      {
        "file.path": path,
        "file.operation": "modified",
        "file.language": "python",
        "file.lines_added": 1,
        "file.lines_removed": 1,
      },
      100,
      { span_id: span, parent_span_id: root },
    );
  };
  const shell = (command: string, ok: boolean, tests: boolean) => (span: string) => {
    const sh = b.span(++k);
    const attrs = { "shell.command": command, "shell.cwd": "/workspace" };
    b.add("shell.command.started", attrs, 100, { span_id: sh, parent_span_id: span });
    b.add(
      ok ? "shell.command.completed" : "shell.command.failed",
      {
        ...attrs,
        "shell.exit_code": ok ? 0 : 1,
        ...(tests
          ? {
              "test.framework": "unittest",
              "test.total": 6,
              "test.failed": ok ? 0 : 1,
              "test.passed": ok ? 6 : 5,
            }
          : {}),
      },
      900,
      { span_id: sh, parent_span_id: span, status: ok ? "success" : "error", duration_ms: 900 },
    );
  };
  const git = (type: string, attrs: Record<string, unknown>) => (span: string) =>
    b.add(type, attrs, 100, { span_id: span, parent_span_id: root });

  b.add("run.started", { "run.name": "Fix OAuth session expiry" }, 0);
  llm();
  tool("read_file", read("app/session.py"));
  tool("read_file", read("tests/test_session.py"));
  llm();
  tool("search");
  llm();
  tool("edit_file", edit("app/session.py"));
  llm();
  tool("run_tests", shell(TEST_COMMAND, false, true));
  llm();
  tool("run_command", shell("tail -n 3300 logs/auth-server.log", true, false));
  tool("read_file", read("app/oauth.py"));
  llm();
  b.add("retry.attempted", { "retry.attempt": 1, "retry.reason": "tests failed" }, 100);
  tool("edit_file", edit(wrong ? "app/oauth.py" : "app/session.py"));
  llm();
  tool("run_tests", shell(TEST_COMMAND, !wrong, true));
  llm();
  if (!wrong) {
    tool("git_commit", git("git.commit", { "git.branch": "fix/oauth-session-expiry" }));
    tool("git_push", git("git.push", { "git.branch": "fix/oauth-session-expiry" }));
    llm();
  }
  b.add(wrong ? "run.failed" : "run.completed", {}, 100, { status: wrong ? "error" : "success" });
  return b.build();
}

export const baselineCodingRun = (): FixtureRun => codingRun(BASELINE_RUN_N, false);
export const wrongFixCodingRun = (): FixtureRun => codingRun(WRONG_FIX_RUN_N, true);
