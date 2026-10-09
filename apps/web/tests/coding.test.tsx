import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CodingSummary } from "@/components/CodingSummary";
import { DiffView } from "@/components/DiffView";
import { EventDrawer } from "@/components/EventDrawer";
import type { EventOut } from "@/lib/api/types";
import {
  artifactId,
  buildStory,
  isCodingRun,
  riskLabel,
  riskTone,
  sensitivePathLabel,
} from "@/lib/coding";
import { diffPath, diffStats, parseDiff } from "@/lib/diff";
import { languageFor, tokenize } from "@/lib/highlight";
import { describe as describeEvent } from "@/lib/timeline";
import { renderWithQuery } from "./helpers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

let n = 0;
const ev = (
  event_type: string,
  attributes: Record<string, unknown> = {},
  extra: Partial<EventOut> = {},
): EventOut =>
  ({
    event_id: `evt_${String(++n).padStart(26, "0")}`,
    run_id: "run_X",
    trace_id: "trc_X",
    span_id: null,
    parent_span_id: null,
    agent_id: "coding-agent",
    event_type,
    occurred_at: "2026-10-07T10:00:00Z",
    sequence: n,
    status: null,
    duration_ms: null,
    attributes,
    tags: [],
    has_payload: false,
    payload: null,
    ...extra,
  }) as unknown as EventOut;

const ART_OUT = "art_01J90000000000000000000031";
const ART_DIFF = "art_01J90000000000000000000030";

describe("highlighter", () => {
  it("tokenizes python without losing a character", () => {
    const line = 'def is_expired(s, now):  # check "x"';
    const tokens = tokenize(line, "python");
    expect(tokens.map((t) => t.text).join("")).toBe(line);
    expect(tokens.find((t) => t.text === "def")?.type).toBe("kw");
    expect(tokens.find((t) => t.text === "is_expired")?.type).toBe("fn");
    expect(tokens.at(-1)?.type).toBe("com");
  });

  it("does not split identifiers at keywords and keeps unterminated strings together", () => {
    expect(tokenize("classify = iffy", "python").every((t) => t.type !== "kw")).toBe(true);
    const t = tokenize('x = "unterminated', "python");
    expect(t.map((x) => x.text).join("")).toBe('x = "unterminated');
    expect(t.some((x) => x.type === "str")).toBe(true);
  });

  it.each([
    ["json", '{"a": 1, "b": true}'],
    ["typescript", "const x = `t${1}`; // c"],
    ["shell", "echo $HOME && ls"],
    ["yaml", "key: value # c"],
    ["plain", "anything <b>"],
  ] as const)("round-trips %s", (lang, line) => {
    expect(
      tokenize(line, lang)
        .map((t) => t.text)
        .join(""),
    ).toBe(line);
  });

  it("leaves pathological lines alone", () => {
    const long = "a".repeat(5000);
    expect(tokenize(long, "python")).toEqual([{ text: long, type: "plain" }]);
    expect(tokenize("", "python")).toEqual([{ text: "", type: "plain" }]);
  });

  it("picks the language from the hint, then the extension", () => {
    expect(languageFor("a/b.py")).toBe("python");
    expect(languageFor("x.unknown")).toBe("plain");
    expect(languageFor("x.txt", "python")).toBe("python");
    expect(languageFor(null)).toBe("plain");
  });
});

const DIFF = `--- a/app/session.py
+++ b/app/session.py
@@ -3,4 +3,5 @@ def f
 def is_expired(s, now):
-    return now > s.expires_at
+    return now >= s.expires_at - SKEW
+    # fixed
 end
\\ No newline at end of file
`;

describe("diff parsing", () => {
  it("numbers lines and counts changes", () => {
    const files = parseDiff(DIFF);
    expect(files).toHaveLength(1);
    expect(diffPath(files[0]!)).toBe("app/session.py");
    expect(diffStats(files)).toEqual({ added: 2, removed: 1 });
    const kinds = files[0]!.lines.map((l) => l.kind);
    expect(kinds).toEqual(["meta", "ctx", "del", "add", "add", "ctx", "meta"]);
    expect(files[0]!.lines[1]).toMatchObject({ oldNo: 3, newNo: 3 });
    expect(files[0]!.lines[3]).toMatchObject({ oldNo: null, newNo: 4 });
  });

  it("splits multi-file git diffs and tolerates garbage", () => {
    const two =
      "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-x\n+y\ndiff --git a/b b/b\n--- /dev/null\n+++ b/b\n@@ -0,0 +1 @@\n+z\n";
    const files = parseDiff(two);
    expect(files.map(diffPath)).toEqual(["a", "b"]);
    expect(parseDiff("")).toEqual([]);
    expect(() => parseDiff("not a diff\n@@ bad\n+++\n\u0000")).not.toThrow();
  });
});

describe("DiffView safety and behaviour", () => {
  it("renders hostile diff content as text only, with no elements beyond its own spans", () => {
    const evil =
      '--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-<img src=x onerror=alert(1)>\n+<script>alert(1)</script> "; </span><b>\n';
    const { container } = renderWithQuery(<DiffView text={evil} lang="python" />);
    expect(container.querySelector("img, script, b")).toBeNull();
    expect(container.innerHTML).not.toContain("<script>");
    expect(container).toHaveTextContent("<script>alert(1)</script>");
    expect(container.querySelector("[onerror]")).toBeNull();
  });

  it("shows signs, line numbers and highlighted tokens", () => {
    const { container } = renderWithQuery(<DiffView text={DIFF} lang="python" />);
    expect(container.querySelectorAll(".diff-add")).toHaveLength(2);
    expect(container.querySelectorAll(".diff-del")).toHaveLength(1);
    expect(container.querySelector(".tok-kw")).not.toBeNull();
    expect(screen.getByLabelText("Diff of app/session.py")).toBeInTheDocument();
    expect(screen.getByText("+2")).toBeInTheDocument();
  });

  it("caps very long diffs until asked", () => {
    const body = Array.from({ length: 2500 }, (_, i) => `+line ${i}`).join("\n");
    const text = `--- a/f\n+++ b/f\n@@ -0,0 +1,2500 @@\n${body}\n`;
    const { container } = renderWithQuery(<DiffView text={text} />);
    expect(container.querySelectorAll(".diff-line").length).toBe(2000);
    fireEvent.click(screen.getByRole("button", { name: /Show all/ }));
    expect(container.querySelectorAll(".diff-line").length).toBe(2501);
  });
});

describe("coding helpers", () => {
  it("parses only well-formed artifact references", () => {
    expect(artifactId(`artifact://${ART_OUT}`)).toBe(ART_OUT);
    for (const bad of [
      "artifact://../x",
      "http://x",
      "artifact://art_short",
      null,
      5,
      "artifact://art_01J90000000000000000000031/../x",
    ])
      expect(artifactId(bad)).toBeNull();
  });

  it("labels risk and sensitive paths", () => {
    expect(riskLabel("R3")).toBe("R3 Privileged or destructive");
    expect(riskLabel(null)).toBeNull();
    expect(riskTone("R4")).toBe("bad");
    expect(riskTone("R2")).toBe("warn");
    expect(riskTone("R0")).toBe("ok");
    expect(sensitivePathLabel(".github/workflows/ci.yml")).toBe("CI/CD configuration");
    expect(sensitivePathLabel("uv.lock")).toBe("dependency lock file");
    expect(sensitivePathLabel("app/session.py")).toBe("authentication code");
    expect(sensitivePathLabel("app/render.py")).toBeNull();
  });

  it("describes file, git and shell events", () => {
    expect(
      describeEvent(
        ev("file.modified", {
          "file.path": "a.py",
          "file.lines_added": 2,
          "file.lines_removed": 1,
        }),
      ),
    ).toBe("a.py +2 -1");
    expect(describeEvent(ev("git.push", { "git.push_target": "origin x" }))).toBe("origin x");
    expect(
      describeEvent(
        ev("shell.command.failed", {
          "shell.command": "t",
          "shell.exit_code": 1,
          "test.total": 5,
          "test.failed": 1,
        }),
      ),
    ).toBe("t · exit 1 · 1 test(s) failed");
  });
});

const failedTests = () =>
  ev("shell.command.failed", {
    "shell.command": "python -m unittest",
    "shell.exit_code": 1,
    "test.framework": "unittest",
    "test.total": 5,
    "test.passed": 4,
    "test.failed": 1,
    "test.failing": ["tests.test_session.T.test_refresh"],
  });
const story = () => [
  ev("run.started"),
  ev("file.read", { "file.path": "app/session.py" }),
  ev("llm.request.completed", { "llm.model": "scripted" }),
  ev("file.modified", {
    "file.path": "app/session.py",
    "file.lines_added": 1,
    "file.lines_removed": 1,
  }),
  ev("shell.command.started", { "shell.command": "python -m unittest" }),
  failedTests(),
  ev("retry.attempted", { "retry.attempt": 1, "retry.reason": "1 test failing" }),
  ev("file.modified", { "file.path": "app/oauth.py" }),
  ev("shell.command.completed", {
    "shell.command": "python -m unittest",
    "shell.exit_code": 0,
    "test.framework": "unittest",
    "test.total": 5,
    "test.passed": 5,
    "test.failed": 0,
  }),
  ev("git.commit", { "git.commit_hash": "abcdef0123456789" }),
];

describe("the run as a story", () => {
  it("reads read, model, edit, failing tests, retry, edit, passing tests, commit", () => {
    const steps = buildStory(story());
    expect(steps.map((s) => s.kind)).toEqual([
      "read",
      "model",
      "edit",
      "test",
      "retry",
      "edit",
      "test",
      "git",
    ]);
    expect(steps[3]).toMatchObject({ tone: "bad", label: "Tests, attempt 1: fail" });
    expect(steps[3]!.detail).toContain("1 failed, 4 passed");
    expect(steps[3]!.detail).toContain("test_refresh");
    expect(steps[6]).toMatchObject({
      tone: "ok",
      label: "Tests, attempt 2: pass",
      detail: "5 of 5 passed",
    });
    expect(steps[4]!.detail).toBe("1 test failing");
    expect(isCodingRun(story())).toBe(true);
    expect(isCodingRun([ev("run.started"), ev("llm.request.completed")])).toBe(false);
  });

  it("tells git work through git steps, not as bare commands", () => {
    const steps = buildStory([
      ev("shell.command.completed", { "shell.command": "git add -A", "shell.exit_code": 0 }),
      ev("shell.command.completed", { "shell.command": "ls", "shell.exit_code": 0 }),
    ]);
    expect(steps.map((s) => s.detail)).toEqual(["ls"]);
  });

  it("lists the steps, opens an event on click, and hides for non-coding runs", () => {
    const open = vi.fn();
    const events = story();
    renderWithQuery(<CodingSummary events={events} onOpen={open} />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(8);
    expect(items[3]).toHaveTextContent("Tests, attempt 1: fail");
    fireEvent.click(items[3]!.querySelector("button")!);
    expect(open).toHaveBeenCalledWith(events[5]!.event_id);
    cleanup();
    renderWithQuery(<CodingSummary events={[ev("run.started")]} onOpen={open} />);
    expect(screen.queryByTestId("story")).toBeNull();
  });
});

function stubArtifacts(contents: Record<string, string>, chunk = 40) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === "string" ? input : input.url);
      calls.push(url.pathname + url.search);
      const m = /artifacts\/([^/]+)\/content$/.exec(url.pathname);
      const text = m ? contents[m[1]!] : undefined;
      if (text === undefined)
        return Response.json(
          { error: { code: "ARTIFACT_NOT_FOUND", message: "x" } },
          { status: 404 },
        );
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const end = Math.min(text.length, offset + chunk);
      return Response.json({
        id: m![1],
        offset,
        next_offset: end < text.length ? end : null,
        total_bytes: text.length,
        content: text.slice(offset, end),
      });
    }),
  );
  return calls;
}

describe("shell panel", () => {
  const big = "line of output\n".repeat(10);
  const event = () => {
    const e = failedTests();
    e.attributes = {
      ...e.attributes,
      "shell.cwd": "/workspace",
      "shell.risk_class": "R1",
      "shell.category": "MODIFY_FILES",
      "shell.stdout_artifact": `artifact://${ART_OUT}`,
      "shell.stdout_bytes": big.length,
      "shell.stderr_bytes": 0,
    };
    e.event_type = "shell.command.failed";
    e.duration_ms = 843;
    return e;
  };

  it("shows command, cwd, duration, exit code, risk and test result without fetching output", () => {
    const calls = stubArtifacts({ [ART_OUT]: big });
    renderWithQuery(<EventDrawer runId="run_X" event={event()} onClose={() => {}} />);
    const panel = screen.getByTestId("shell-panel");
    expect(panel).toHaveTextContent("python -m unittest");
    expect(panel).toHaveTextContent("/workspace");
    expect(panel).toHaveTextContent("843 ms");
    expect(screen.getByTestId("exit-code")).toHaveTextContent("✕ 1");
    expect(screen.getByTestId("risk-class")).toHaveTextContent("R1 Local, reversible change");
    expect(screen.getByTestId("test-result")).toHaveTextContent("✕ Tests failed");
    expect(screen.getByTestId("test-result")).toHaveTextContent(
      "tests.test_session.T.test_refresh",
    );
    expect(screen.getByTestId("artifact-stderr")).toHaveTextContent("not captured");
    expect(calls.filter((c) => c.includes("/artifacts/"))).toEqual([]); // lazy: nothing fetched yet
  });

  it("loads output only on request, then more on demand", async () => {
    const calls = stubArtifacts({ [ART_OUT]: big });
    renderWithQuery(<EventDrawer runId="run_X" event={event()} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Show stdout/ }));
    await screen.findByLabelText("stdout");
    expect(calls.filter((c) => c.includes("/content"))).toHaveLength(1);
    expect(screen.getByLabelText("stdout").textContent!.length).toBe(40);
    expect(screen.getByTestId("artifact-progress")).toHaveTextContent("40 of 150 bytes loaded");
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getByLabelText("stdout").textContent!.length).toBe(80));
    expect(calls.some((c) => c.includes("offset=40"))).toBe(true);
  });

  it("renders terminal output containing markup as text", async () => {
    stubArtifacts({ [ART_OUT]: "<img src=x onerror=alert(1)><script>1</script>" }, 500);
    renderWithQuery(<EventDrawer runId="run_X" event={event()} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Show stdout/ }));
    const pre = await screen.findByLabelText("stdout");
    expect(pre).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(pre.querySelector("img, script")).toBeNull();
  });

  it("says so when an artifact is missing, and for a started event shows no output controls", async () => {
    stubArtifacts({});
    renderWithQuery(<EventDrawer runId="run_X" event={event()} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Show stdout/ }));
    expect(await screen.findByText(/Not available/)).toBeInTheDocument();
    cleanup();
    renderWithQuery(
      <EventDrawer
        runId="run_X"
        event={ev("shell.command.started", { "shell.command": "ls" })}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /stdout/ })).toBeNull();
  });
});

describe("file and git panels", () => {
  it("loads and highlights the diff, flags sensitive paths, and explains withheld diffs", async () => {
    stubArtifacts({ [ART_DIFF]: DIFF }, 100000);
    const e = ev("file.modified", {
      "file.path": "app/session.py",
      "file.language": "python",
      "file.lines_added": 2,
      "file.lines_removed": 1,
      "file.hash_before": "sha256:aaaaaaaaaaaaaaaa",
      "file.hash_after": "sha256:bbbbbbbbbbbbbbbb",
      "diff.artifact": `artifact://${ART_DIFF}`,
    });
    const { container } = renderWithQuery(
      <EventDrawer runId="run_X" event={e} onClose={() => {}} />,
    );
    expect(await screen.findByTestId("diff")).toBeInTheDocument();
    expect(container.querySelectorAll(".diff-add")).toHaveLength(2);
    expect(screen.getByTestId("sensitive-path")).toHaveTextContent("authentication code");
    expect(screen.getByTestId("file-section")).toHaveTextContent("aaaaaaaaaa → bbbbbbbbbb");
    cleanup();
    const w = ev("file.created", { "file.path": ".env", "diff.withheld": "sensitive_path" });
    renderWithQuery(<EventDrawer runId="run_X" event={w} onClose={() => {}} />);
    expect(screen.getByTestId("diff-withheld")).toHaveTextContent("secrets");
  });

  it("shows git details", () => {
    const e = ev("git.push", { "git.push_target": "origin fix/x", "git.branch": "fix/x" });
    renderWithQuery(<EventDrawer runId="run_X" event={e} onClose={() => {}} />);
    expect(screen.getByTestId("git-section")).toHaveTextContent("origin fix/x");
  });

  it("reads never show content", () => {
    renderWithQuery(
      <EventDrawer
        runId="run_X"
        event={ev("file.read", { "file.path": "a.py" })}
        onClose={() => {}}
      />,
    );
    expect(screen.getByTestId("file-section")).toHaveTextContent("never the content");
  });
});
