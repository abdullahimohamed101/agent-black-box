import { fixtureSearch, FixtureQueryError } from "@/fixtures/search";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_FILTERS, RunsList, type ListFilters } from "@/components/RunsList";
import { parseFilters } from "@/components/RunsListPage";
import { RunsTable } from "@/components/RunsTable";
import { ApiRequestError } from "@/lib/api/client";
import { fixtureRuns } from "@/fixtures";
import { shouldRetry } from "@/lib/queryClient";
import {
  appendTerm,
  convertLegacy,
  effectiveQuery,
  fieldTerm,
  quoteValue,
  removeSpan,
  searchProblem,
  termText,
} from "@/lib/search";
import { BASE, renderWithQuery, stubApi } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

function Harness({ initial }: { initial?: Partial<ListFilters> }) {
  const [f, setF] = useState<ListFilters>({ ...DEFAULT_FILTERS, ...initial });
  return (
    <RunsList base={BASE} filters={f} onFilters={setF} now={Date.parse("2026-10-08T00:00:00Z")} />
  );
}

const envelope = (status: number, code: string, message: string, details: object = {}) =>
  Response.json(
    { error: { code, message, retryable: status >= 500, request_id: "req_t", details } },
    { status },
  );

const FIELDS = [
  { name: "status", kind: "enum", description: "run status", ordered: false, prefix: false },
  { name: "tool", kind: "name", description: "tool name", ordered: false, prefix: true },
];
const term = (field: string, value: string, span: [number, number], extra = {}) => ({
  kind: "field",
  field,
  op: "=",
  value,
  value_end: null,
  lexemes: null,
  phrase: false,
  prefix: false,
  negated: false,
  span,
  ...extra,
});
const echo = (terms: object[], extra: object = {}) => ({
  terms,
  fields: FIELDS,
  content_allowed: false,
  unindexed: null,
  unindexed_capped: false,
  ...extra,
});
const emptyFacets = { status: [], agents: [], tools: [], models: [], event_types: [] };

describe("search helpers", () => {
  it("cuts and removes a term by the server's span and tidies whitespace", () => {
    const q = 'status:failed  tool:github "npm test"';
    expect(termText(q, [15, 26])).toBe("tool:github");
    expect(removeSpan(q, [15, 26])).toBe('status:failed "npm test"');
    expect(removeSpan(q, [0, 13])).toBe('tool:github "npm test"');
    // a stale or hostile span changes nothing
    expect(removeSpan(q, [5, 999])).toBe(q);
    expect(removeSpan(q, [-1, 3])).toBe(q);
    expect(termText(q, [4, 4])).toBe("");
    expect(termText(q, [0.5, 3])).toBe("");
  });

  it("quotes facet values so one click stays one term", () => {
    expect(quoteValue("github")).toBe("github");
    expect(quoteValue("claude 3")).toBe('"claude 3"');
    expect(quoteValue('a"b\\c')).toBe('"a\\"b\\\\c"');
    expect(fieldTerm("model", "my model")).toBe('model:"my model"');
    expect(appendTerm("", "tool:x")).toBe("tool:x");
    expect(appendTerm("  status:failed ", "tool:x")).toBe("status:failed tool:x");
  });

  it("converts a legacy filter URL to q (plan F11) and keeps several statuses as a parameter", () => {
    expect(convertLegacy(new URLSearchParams("status=FAILED&agent=x&range=24h"))).toEqual({
      q: "status:failed agent:x",
      statuses: [],
      agent: "",
    });
    expect(convertLegacy(new URLSearchParams("status=FAILED,BLOCKED&q=tool:a"))).toEqual({
      q: "tool:a",
      statuses: ["FAILED", "BLOCKED"],
      agent: "",
    });
    expect(convertLegacy(new URLSearchParams("agent=Bad Agent!")).agent).toBe("Bad Agent!");
    // review F2: ids with `.` and `_` are valid agent ids and valid `agent:` values
    expect(convertLegacy(new URLSearchParams("agent=my_agent&status=FAILED"))).toEqual({
      q: "status:failed agent:my_agent",
      statuses: [],
      agent: "",
    });
    expect(convertLegacy(new URLSearchParams("agent=v1.2-bot")).q).toBe("agent:v1.2-bot");
    const f = parseFilters(new URLSearchParams("status=FAILED&agent=x&range=24h"));
    expect(f).toMatchObject({ q: "status:failed agent:x", range: "24h", statuses: [], agent: "" });
    // anything that is not a status name is dropped, never forwarded into q
    expect(parseFilters(new URLSearchParams("status=%22%20OR%201,x")).q).toBe("");
  });

  it("writes the range control as a relative-time term", () => {
    expect(effectiveQuery("status:failed", "24h")).toBe("status:failed started:>=-24h");
    expect(effectiveQuery("", "7d")).toBe("started:>=-7d");
    expect(effectiveQuery("  ", "all")).toBe("");
  });

  it("classifies the API's refusals and nothing else", () => {
    const err = (status: number, code: string, details = {}) =>
      new ApiRequestError("m", status, code, null, status >= 500, details);
    expect(searchProblem(err(422, "QUERY_INVALID", { position: 4 }))).toMatchObject({
      kind: "invalid",
      position: 4,
    });
    expect(searchProblem(err(422, "QUERY_INVALID", { position: -2 }))).toMatchObject({
      position: null,
    });
    expect(searchProblem(err(403, "PERMISSION_DENIED", { field: "file" }))).toEqual({
      kind: "forbidden",
      field: "file",
    });
    // a field name that is not a plain identifier is not echoed
    expect(searchProblem(err(403, "PERMISSION_DENIED", { field: "<b>x</b>" }))).toEqual({
      kind: "forbidden",
      field: null,
    });
    expect(searchProblem(err(414, "QUERY_TOO_LONG"))).toEqual({ kind: "too-long" });
    expect(searchProblem(err(503, "SEARCH_BUSY"))?.kind).toBe("busy");
    expect(searchProblem(err(503, "SEARCH_TIMEOUT"))?.kind).toBe("timeout");
    expect(searchProblem(err(503, "API_UNREACHABLE"))).toBeNull();
    expect(searchProblem(new Error("x"))).toBeNull();
  });

  it("does not auto-retry a busy or timed-out search", () => {
    const e = (code: string) => new ApiRequestError("m", 503, code, null, true);
    expect(shouldRetry(0, e("SEARCH_BUSY"))).toBe(false);
    expect(shouldRetry(0, e("SEARCH_TIMEOUT"))).toBe(false);
    expect(shouldRetry(0, e("API_UNREACHABLE"))).toBe(true);
  });
});

describe("search page", () => {
  it("sends q percent-encoded and renders chips from the server's parse; removing a chip rewrites q", async () => {
    const seen: string[] = [];
    stubApi({
      "/v1/runs": (url) => {
        seen.push(url.search);
        const q = url.searchParams.get("q");
        if (!q) return Response.json({ items: [], next_cursor: null, query: echo([]) });
        const runs = fixtureRuns()
          .slice(0, 2)
          .map((f) => f.run);
        const terms = q.includes("tool:a+b")
          ? [term("status", "failed", [0, 13]), term("tool", "a+b", [14, 24])]
          : [term("status", "failed", [0, 13])];
        return Response.json({
          items: runs,
          next_cursor: null,
          query: echo(terms),
          facets: emptyFacets,
        });
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await screen.findByText("No runs yet");
    await user.type(
      screen.getByRole("searchbox", { name: "Search" }),
      "status:failed tool:a+b&x{Enter}",
    );
    const chips = await screen.findByRole("list", { name: "Search terms" });
    expect(within(chips).getAllByRole("listitem")).toHaveLength(2);
    expect(within(chips).getByText("tool:a+b&x")).toBeInTheDocument();
    // the plus and ampersand travel encoded: one parameter, intact
    const last = seen.filter((s) => s.includes("q=")).pop()!;
    expect(new URLSearchParams(last).get("q")).toBe("status:failed tool:a+b&x");
    expect(last).not.toContain("tool:a+b&x");
    await user.click(within(chips).getByRole("button", { name: "Remove tool:a+b&x" }));
    await waitFor(() =>
      expect(screen.getByRole("searchbox", { name: "Search" })).toHaveValue("status:failed"),
    );
  });

  it("renders hostile query text as text, never markup", async () => {
    const hostile = '<img src=x onerror="alert(1)"> "<script>x</script>"';
    stubApi({
      "/v1/runs": (url) =>
        url.searchParams.get("q")
          ? Response.json({
              items: fixtureRuns()
                .slice(0, 1)
                .map((f) => f.run),
              next_cursor: null,
              query: echo([{ ...term("tool", "x", [0, hostile.length]) }]),
              facets: emptyFacets,
            })
          : Response.json({ items: [], next_cursor: null, query: echo([]) }),
    });
    const { container } = render(<div />);
    container.remove();
    renderWithQuery(<Harness initial={{ q: hostile }} />);
    const chips = await screen.findByRole("list", { name: "Search terms" });
    expect(chips.textContent).toContain("<img src=x");
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
  });

  it("shows a 422 with a caret under the position the API gave", async () => {
    stubApi({
      "/v1/runs": (url) =>
        url.searchParams.get("q")
          ? envelope(422, "QUERY_INVALID", "unknown field", {
              position: 7,
              reason: "unknown field",
            })
          : Response.json({ items: [], next_cursor: null, query: echo([]) }),
    });
    renderWithQuery(<Harness initial={{ q: "tool:a bogus:1" }} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This search could not be understood");
    expect(alert).toHaveTextContent("unknown field");
    expect(alert).toHaveTextContent("character 8");
    expect(alert.querySelector(".search-caret")?.textContent).toBe("tool:a bogus:1\n       ^");
    expect(screen.queryByText(/No runs match/)).toBeNull();
  });

  it("explains a 403 for a content field and never says there were no results", async () => {
    const secret = "id_rsa-s3cr3t";
    stubApi({
      "/v1/runs": (url) =>
        url.searchParams.get("q")
          ? envelope(403, "PERMISSION_DENIED", "You do not have permission.", { field: "file" })
          : Response.json({ items: [], next_cursor: null, query: echo([]) }),
    });
    const { container } = renderWithQuery(<Harness initial={{ q: `file:${secret}` }} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your role cannot search that field");
    expect(alert).toHaveTextContent("file:");
    expect(alert).toHaveTextContent("content permission");
    expect(alert.textContent).not.toContain(secret);
    expect(container.textContent).not.toMatch(/No runs match|No runs yet/);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows a 414 from the proxy like a 422, without a caret", async () => {
    stubApi({
      "/v1/runs": (url) =>
        url.searchParams.get("q")
          ? envelope(414, "QUERY_TOO_LONG", "Query string too long.")
          : Response.json({ items: [], next_cursor: null, query: echo([]) }),
    });
    renderWithQuery(<Harness initial={{ q: "tool:x" }} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This search is too long");
    expect(alert.querySelector(".search-caret")).toBeNull();
  });

  it("refuses an over-long query locally with the same message and sends nothing", async () => {
    const calls = stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await screen.findAllByRole("row");
    const before = calls.length;
    const box = screen.getByRole("searchbox", { name: "Search" });
    await user.click(box);
    await user.paste("a".repeat(513));
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("This search is too long");
    expect(calls.length).toBe(before);
  });

  it.each([
    ["SEARCH_BUSY", "Search is busy"],
    ["SEARCH_TIMEOUT", "This search took too long"],
  ])("%s shows a retry that runs the search again", async (code, title) => {
    let fail = true;
    stubApi({
      "/v1/runs": (url) =>
        url.searchParams.get("q") && fail
          ? envelope(503, code, "Try again in a moment.")
          : Response.json({
              items: url.searchParams.get("q")
                ? fixtureRuns()
                    .slice(0, 1)
                    .map((f) => f.run)
                : [],
              next_cursor: null,
              query: echo(url.searchParams.get("q") ? [term("status", "failed", [0, 13])] : []),
              facets: emptyFacets,
            }),
    });
    const user = userEvent.setup();
    renderWithQuery(<Harness initial={{ q: "status:failed" }} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(title);
    fail = false;
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getAllByRole("row").length).toBeGreaterThan(1));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the unindexed notice, the matched column, the truncation badge and facets that append terms", async () => {
    const runs = fixtureRuns()
      .slice(0, 2)
      .map((f) => f.run);
    const seen: string[] = [];
    stubApi({
      "/v1/runs": (url) => {
        const q = url.searchParams.get("q");
        if (q) seen.push(q);
        if (!q) return Response.json({ items: [], next_cursor: null, query: echo([]) });
        return Response.json({
          items: [
            {
              ...runs[0]!,
              matched: {
                tools: ["github"],
                models: [],
                event_types: [],
                agents: [],
                tags: [],
                metadata_truncated: true,
                content_truncated: false,
              },
            },
            {
              ...runs[1]!,
              matched: {
                tools: ["github"],
                models: ["model-x"],
                event_types: [],
                agents: ["a"],
                tags: [],
                metadata_truncated: false,
              },
            },
          ],
          next_cursor: null,
          query: echo([term("tool", "github", [0, 11])], {
            unindexed: 12,
            unindexed_capped: false,
          }),
          facets: {
            ...emptyFacets,
            status: [{ value: "FAILED", count: 2 }],
            models: [{ value: "model x", count: 1 }],
          },
        });
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<Harness initial={{ q: "tool:github" }} />);
    expect(await screen.findByTestId("unindexed")).toHaveTextContent("12 runs are not yet indexed");
    const cells = await screen.findAllByTestId("matched");
    expect(cells).toHaveLength(2);
    expect(cells[0]).toHaveTextContent("tool:github");
    expect(cells[0]).toHaveTextContent("index truncated");
    expect(cells[1]).not.toHaveTextContent("index truncated");
    expect(cells[1]).toHaveTextContent("model:model-x");
    await user.click(screen.getByRole("button", { name: /model x/ }));
    await waitFor(() => expect(seen).toContain('tool:github model:"model x"'));
  });

  it("offers agent ids with dots and underscores, and leaves (other) as a plain label (review F2)", async () => {
    const runs = fixtureRuns()
      .slice(0, 1)
      .map((f) => f.run);
    const seen: string[] = [];
    stubApi({
      "/v1/runs": (url) => {
        const q = url.searchParams.get("q");
        if (q) seen.push(q);
        if (!q) return Response.json({ items: [], next_cursor: null, query: echo([]) });
        return Response.json({
          items: [runs[0]!],
          next_cursor: null,
          query: echo([term("tool", "github", [0, 11])]),
          facets: {
            ...emptyFacets,
            agents: [
              { value: "my_agent", count: 3 },
              { value: "v1.2-bot", count: 2 },
              { value: "(other)", count: 1 },
            ],
          },
        });
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<Harness initial={{ q: "tool:github" }} />);
    expect(await screen.findByRole("button", { name: /my_agent/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /v1\.2-bot/ })).toBeInTheDocument();
    expect(screen.getByText("(other)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /\(other\)/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: /v1\.2-bot/ }));
    await waitFor(() => expect(seen).toContain("tool:github agent:v1.2-bot"));
  });

  it("fixture mode reads the API parser's canonical examples the same way for its field subset (review F10)", () => {
    // [q, field, negated, value, prefix]: the expectations of apps/api/tests/search/test_parser.py
    const shared: [string, string, boolean, string, boolean][] = [
      ["status:failed", "status", false, "failed", false],
      ["tool:github", "tool", false, "github", false],
      ["event:shell.*", "event", false, "shell.", true],
      ['model:"claude*"', "model", false, "claude", true],
      ["agent:planner", "agent", false, "planner", false],
      ["agent:my_agent", "agent", false, "my_agent", false],
      ["agent:V1.2-Bot", "agent", false, "v1.2-bot", false],
      ["agent:plan*", "agent", false, "plan", true],
      ["-tool:github", "tool", true, "github", false],
      ["name:deploy", "name", false, "deploy", false],
    ];
    for (const [q, field, negated, value, prefix] of shared) {
      const { query } = fixtureSearch(fixtureRuns(), q);
      expect(query.terms, q).toHaveLength(1);
      const t = query.terms[0]!;
      expect([t.field, t.negated, t.value?.toLowerCase(), t.prefix], q).toEqual([
        field,
        negated,
        value,
        prefix,
      ]);
    }
    // what the fixtures cannot answer is refused, never answered differently
    for (const q of ["file:x", "command:curl", "cost:>1"])
      expect(() => fixtureSearch(fixtureRuns(), q), q).toThrow(FixtureQueryError);
  });

  it("counts a capped unindexed figure as N+", async () => {
    stubApi({
      "/v1/runs": (url) =>
        Response.json({
          items: [],
          next_cursor: null,
          query: echo([term("tool", "x", [0, 6])], { unindexed: 1000, unindexed_capped: true }),
          facets: emptyFacets,
          ...(url.searchParams.get("q") ? {} : {}),
        }),
    });
    renderWithQuery(<Harness initial={{ q: "tool:x" }} />);
    expect(await screen.findByTestId("unindexed")).toHaveTextContent(
      "1000+ runs are not yet indexed",
    );
    expect(await screen.findByText("No runs match these filters")).toBeInTheDocument();
  });

  it("lists exactly the fields the API allows: a viewer is never offered file or command", async () => {
    stubApi({ "/v1/runs": () => Response.json({ items: [], next_cursor: null, query: echo([]) }) });
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await screen.findByText("No runs yet");
    await user.click(screen.getByText("Search help"));
    const list = await screen.findByRole("list", { name: "Searchable fields" });
    expect(list.textContent).toContain("status:");
    expect(list.textContent).toContain("tool:");
    expect(list.textContent).not.toMatch(/\bfile:|\bcommand:/);
  });

  it("an owner's catalogue offers the content fields the API lists", async () => {
    stubApi({
      "/v1/runs": () =>
        Response.json({
          items: [],
          next_cursor: null,
          query: echo([], {
            content_allowed: true,
            fields: [
              ...FIELDS,
              {
                name: "file",
                kind: "glob",
                description: "file path",
                ordered: false,
                prefix: false,
              },
            ],
          }),
        }),
    });
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await screen.findByText("No runs yet");
    await user.click(screen.getByText("Search help"));
    expect((await screen.findByRole("list", { name: "Searchable fields" })).textContent).toContain(
      "file:",
    );
  });

  it("the range control removes with its chip", async () => {
    stubApi({
      "/v1/runs": (url) => {
        const q = url.searchParams.get("q");
        return Response.json({
          items: q
            ? fixtureRuns()
                .slice(0, 1)
                .map((f) => f.run)
            : [],
          next_cursor: null,
          query: echo(q ? [term("started", "x", [0, 14], { op: ">=" })] : []),
          facets: emptyFacets,
        });
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<Harness initial={{ range: "24h" }} />);
    const chips = await screen.findByRole("list", { name: "Search terms" });
    expect(within(chips).getByText("started:>=-24h")).toBeInTheDocument();
    await user.click(within(chips).getByRole("button", { name: "Remove started:>=-24h" }));
    await waitFor(() => expect(screen.getByLabelText("Started")).toHaveValue("all"));
  });
});

describe("fixture search", () => {
  it("answers the fixture subset through the same client path", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(8));
    await user.type(screen.getByRole("searchbox", { name: "Search" }), "status:failed{Enter}");
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(2));
    await user.clear(screen.getByRole("searchbox", { name: "Search" }));
    await user.type(screen.getByRole("searchbox", { name: "Search" }), "command:curl{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("fixture mode");
  });
});

describe("RunsTable matched column", () => {
  it("is absent without matched data", () => {
    const runs = fixtureRuns().map((f) => f.run);
    render(<RunsTable runs={runs} base={BASE} caption="Runs" />);
    expect(screen.queryByText("Matched")).toBeNull();
  });
});
