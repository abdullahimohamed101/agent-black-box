import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EvidenceList } from "@/components/EvidenceList";
import { RunDetail } from "@/components/RunDetail";
import { fixtureReply } from "@/fixtures/api";
import { baselineCodingRun } from "@/fixtures/codingRuns";
import type { EvidenceRow } from "@/lib/evidence";
import { BASE, renderWithQuery, stubApi } from "./helpers";

const nav = vi.hoisted(() => ({ query: "view=evidence" }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs/x",
  useSearchParams: () => new URLSearchParams(nav.query),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  nav.query = "view=evidence";
});

const art = (k: number) => `artifact://art_${String(k).padStart(26, "0")}`;

/** The coding fixture carries no artifacts (it has no artifact store); give its edits and test runs some. */
function withArtifacts() {
  stubApi({
    "/events": (url) => {
      const segs = url.pathname.replace(/^\/api\/abb\//, "").split("/");
      const r = fixtureReply(segs, url.searchParams);
      const body = r.body as {
        items: { event_type: string; attributes: Record<string, unknown> }[];
      };
      let k = 0;
      body.items = body.items.map((e) => {
        if (e.event_type === "file.modified")
          return { ...e, attributes: { ...e.attributes, "diff.artifact": art(++k) } };
        if (e.event_type === "shell.command.failed")
          return {
            ...e,
            attributes: {
              ...e.attributes,
              "shell.stdout_artifact": art(++k),
              "shell.stdout_bytes": 512,
            },
          };
        return e;
      });
      return Response.json(body, { status: r.status });
    },
  });
}

describe("Evidence view", () => {
  it("lists the artifacts of a coding run and opens the inspector from a row", async () => {
    withArtifacts();
    const user = userEvent.setup();
    const { run } = baselineCodingRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const region = await screen.findByRole("region", { name: "Captured artifacts" });
    expect(screen.getByRole("tab", { name: "Evidence" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "Evidence" })).toBeInTheDocument();
    const table = await within(region).findByRole("table", { name: "Evidence" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3); // two edits, one failed test command
    expect(within(rows[0]!).getByText("diff")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("app/session.py")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("stdout")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("512 bytes")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("progress")).toHaveTextContent(/^3 artifacts in \d+ events/),
    );

    await user.click(
      within(rows[0]!).getByRole("button", { name: /^Open diff from file\.modified/ }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "file.modified" })).toBeInTheDocument();
    // the row that opened it is marked, and Escape puts focus back on the evidence region
    expect(within(table).getAllByRole("row")[1]).toHaveAttribute("data-selected");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(region).toHaveFocus());
  });

  it("says so when the run captured no artifacts", async () => {
    stubApi();
    const { run } = baselineCodingRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    expect(await screen.findByTestId("evidence-empty")).toHaveTextContent(
      "No artifacts were captured in this run.",
    );
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("0 artifacts"));
  });

  it("switching views keeps the URL in step", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = baselineCodingRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("region", { name: "Captured artifacts" });
    await user.click(screen.getByRole("tab", { name: "Timeline" }));
    expect(await screen.findByRole("listbox", { name: /Run timeline/ })).toBeInTheDocument();
    expect(window.location.search).not.toContain("view=");
    await user.click(screen.getByRole("tab", { name: "Evidence" }));
    expect(window.location.search).toContain("view=evidence");
  });
});

describe("EvidenceList rendering", () => {
  const row = (over: Partial<EvidenceRow>): EvidenceRow => ({
    key: "k",
    kind: "stdout",
    eventId: "evt_1",
    eventType: "shell.command.completed",
    sequence: 1,
    occurredAt: "2026-10-07T10:00:00Z",
    what: null,
    bytes: null,
    artifactId: "art_1",
    ...over,
  });

  it("renders hostile text as text and the withheld wording as is", () => {
    const hostile = '<img src=x onerror="alert(1)"> && rm -rf /';
    const { container } = renderWithQuery(
      <EvidenceList
        rows={[
          row({ what: hostile }),
          row({ key: "k2", eventId: "evt_2", what: "content withheld for your role" }),
        ]}
        t0={Date.parse("2026-10-07T10:00:00Z")}
        selectedEventId={null}
        onOpen={() => undefined}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(hostile)).toBeInTheDocument();
    expect(screen.getByText("content withheld for your role")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("[withheld]");
  });
});
