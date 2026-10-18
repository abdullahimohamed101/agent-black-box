import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EventDrawer } from "@/components/EventDrawer";
import { RunDetail } from "@/components/RunDetail";
import { fixtureReply } from "@/fixtures/api";
import { baselineCodingRun } from "@/fixtures/codingRuns";
import { fixtureRuns } from "@/fixtures";
import { API_WITHHELD, isContentAttribute, withholdEvent } from "@/fixtures/withhold";
import { multiAgentRun } from "@/fixtures/runs";
import { WITHHELD_TEXT } from "@/lib/withheld";
import { BASE, renderWithQuery } from "./helpers";

// The fixture viewer must project like the API for a role without payload.read, or every "the viewer sees no content"
// test is vacuous: these tests use the same projection the fixtures server applies for /w/viewer-only and fail if a
// command, a path, the API's marker or a span name reaches text, a title, an aria-label or any data-* attribute.
const nav = vi.hoisted(() => ({ query: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs/x",
  useSearchParams: () => new URLSearchParams(nav.query),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  nav.query = "";
});

function stubViewer() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === "string" ? input : input.url);
      const segs = url.pathname.replace(/^\/api\/abb\//, "").split("/");
      const r = fixtureReply(segs, url.searchParams, { content: false });
      return Response.json(r.body, { status: r.status });
    }),
  );
}

/** Every captured value the viewer must not see: content attributes and content-named span names of one run. */
function secretsOf(runId: string): string[] {
  const f = fixtureRuns().find((x) => x.run.id === runId)!;
  const out = new Set<string>();
  for (const e of f.events)
    for (const [k, v] of Object.entries(e.attributes))
      if (isContentAttribute(k) && typeof v === "string" && v.length >= 3) out.add(v);
  const eventTypes = new Set(f.events.map((e) => e.event_type));
  for (const s of f.spans)
    if (s.name && ["shell", "file", "git"].includes(s.kind ?? "") && !eventTypes.has(s.name))
      out.add(s.name);
  return [...out];
}

/** Text and every attribute value of the rendered document, where a leak could hide. */
function surface(): string {
  const parts: string[] = [document.body.textContent ?? ""];
  for (const el of document.body.querySelectorAll("*"))
    for (const a of el.attributes) parts.push(a.value);
  return parts.join("\n");
}

function expectNoLeak(secrets: string[]) {
  const s = surface();
  expect(s).not.toContain(API_WITHHELD);
  for (const secret of secrets) expect(s, `leaked: ${secret}`).not.toContain(secret);
}

describe("the fixture viewer projects like the API", () => {
  it("withholds content attributes, payloads and span names, with the API's marker and key list", () => {
    const f = baselineCodingRun();
    const cmd = f.events.find((e) => typeof e.attributes["shell.command"] === "string")!;
    const list = fixtureReply(
      ["v1", "runs", f.run.id, "events"],
      new URLSearchParams("limit=500"),
      { content: false },
    ).body as { items: ReturnType<typeof withholdEvent>[] };
    const got = list.items.find((e) => e.event_id === cmd.event_id)!;
    expect(got.attributes["shell.command"]).toBe(API_WITHHELD);
    expect(got.withheld_attributes).toContain("shell.command");
    expect(got.payload).toBeNull();
    const detail = fixtureReply(
      ["v1", "runs", f.run.id, "events", cmd.event_id],
      new URLSearchParams(),
      { content: false },
    ).body as { payload_withheld: boolean };
    expect(detail.payload_withheld).toBe(true);
    const spans = fixtureReply(["v1", "runs", f.run.id, "spans"], new URLSearchParams(), {
      content: false,
    }).body as { items: { kind: string | null; name: string | null; name_withheld: boolean }[] };
    expect(spans.items.some((s) => s.name_withheld && s.name === null)).toBe(true);
    // the owner is untouched
    const owner = fixtureReply(["v1", "runs", f.run.id, "events"], new URLSearchParams()).body as {
      items: { attributes: Record<string, unknown> }[];
    };
    expect(JSON.stringify(owner)).not.toContain(API_WITHHELD);
  });
});

describe("a viewer sees the withheld wording and no captured content", () => {
  it("coding run: timeline and context", async () => {
    stubViewer();
    const { run } = baselineCodingRun();
    const secrets = secretsOf(run.id);
    expect(secrets.length).toBeGreaterThan(3);
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const list = await screen.findByRole("listbox", { name: /Run timeline/ });
    await waitFor(() => expect(within(list).getAllByRole("option").length).toBeGreaterThan(5));
    expect(document.body.textContent).toContain(WITHHELD_TEXT);
    expectNoLeak(secrets);
  });

  it("coding run: the inspector on every event that carries content", async () => {
    stubViewer();
    const f = baselineCodingRun();
    const secrets = secretsOf(f.run.id);
    const carrying = f.events.filter((e) => Object.keys(e.attributes).some(isContentAttribute));
    expect(carrying.length).toBeGreaterThan(3);
    for (const e of carrying) {
      const { unmount } = renderWithQuery(
        <EventDrawer runId={f.run.id} event={withholdEvent(e, false)} onClose={() => {}} />,
      );
      const dialog = screen.getByRole("dialog");
      // wait for the detail request, which carries payload_withheld
      await waitFor(() => expect(dialog.textContent).toContain(WITHHELD_TEXT));
      expectNoLeak(secrets);
      unmount();
    }
  });

  it("coding run: evidence view", async () => {
    stubViewer();
    nav.query = "view=evidence";
    const { run } = baselineCodingRun();
    const secrets = secretsOf(run.id);
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("region", { name: "Captured artifacts" });
    expect(screen.getByRole("tab", { name: "Evidence" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent(/events/));
    expectNoLeak(secrets);
  });

  it("multi-agent run: waterfall, agent tree and context", async () => {
    stubViewer();
    nav.query = "view=waterfall";
    const { run } = multiAgentRun();
    const secrets = secretsOf(run.id);
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByTestId("waterfall");
    await screen.findByTestId("agent-tree");
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent(/events/));
    expectNoLeak(secrets);
  });
});
