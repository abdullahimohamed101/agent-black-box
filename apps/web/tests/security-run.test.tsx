import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RunDetail } from "@/components/RunDetail";
import { SecurityFindings } from "@/components/SecurityFindings";
import { fixtureRuns } from "@/fixtures";
import { SECURITY_SENTINELS } from "@/fixtures/securityRuns";
import type { FindingOut } from "@/lib/api/types";
import { BASE, renderWithQuery, stubApi } from "./helpers";

let search = "";
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs/x",
  useSearchParams: () => new URLSearchParams(search),
}));
beforeEach(() => {
  search = "";
});
afterEach(() => vi.unstubAllGlobals());

const leaked = () => fixtureRuns().find((f) => f.run.name === "Deploy with leaked key")!;
const exportLogs = () => fixtureRuns().find((f) => f.run.name === "Export build logs")!;

describe("security on the run page (Phase 13 D5)", () => {
  it("shows a quiet chip beside the status, singular for one, only when there are findings", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={leaked().run.id} base={BASE} />);
    const chip = await screen.findByTestId("security-chip");
    expect(chip).toHaveTextContent("6 security findings");
    expect(chip).toHaveAccessibleName(/6 security findings \(most severe: Critical\)/);
    expect(chip.closest(".run-title")).not.toBeNull();
  });

  it("has no chip for a run without security findings", async () => {
    stubApi();
    const clean = fixtureRuns().find((f) => f.run.name === "Fix login session expiry")!;
    renderWithQuery(<RunDetail runId={clean.run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    expect(screen.queryByTestId("security-chip")).toBeNull();
  });

  it("the chip opens the Context column on the Security group (keyboard works)", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={leaked().run.id} base={BASE} />);
    const chip = await screen.findByTestId("security-chip");
    await screen.findByTestId("security-group");
    expect(screen.getByRole("button", { name: /^Context/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    chip.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: /^Context/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(document.activeElement).toBe(document.getElementById("security-h"));
  });

  it("the Security group sits above the reliability findings and lists one line per finding", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={leaked().run.id} base={BASE} />);
    const group = await screen.findByTestId("security-group");
    const lines = await within(group).findAllByTestId("security-finding");
    expect(lines).toHaveLength(6);
    expect(lines[0]).toHaveAttribute("data-severity", "critical"); // most severe first
    expect(within(lines[0]!).getByText("Critical")).toBeInTheDocument();
    expect(within(lines[0]!).getByRole("heading", { level: 3 })).toHaveTextContent(
      "a credential appeared in captured text",
    );
    expect(lines[0]).toHaveTextContent("Rule: a redaction record names a credential pattern");
    const findings = screen.getByTestId("findings");
    expect(group.compareDocumentPosition(findings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // security findings are not in the reliability list
    expect(screen.queryAllByTestId("finding")).toHaveLength(0);
  });

  it("'Show evidence' opens the event the finding cites", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={leaked().run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await user.click(screen.getByRole("button", { name: /^Context/ }));
    const lines = await screen.findAllByTestId("security-finding");
    const file = lines.find((l) => l.textContent?.includes("usually holds secrets"))!;
    await user.click(within(file).getByRole("button", { name: "Show evidence" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("file.read");
  });

  it("a finding with no event link says so instead of offering a button", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={exportLogs().run.id} base={BASE} />);
    const lines = await screen.findAllByTestId("security-finding");
    const cred = lines.find(
      (l) => l.dataset.severity === "critical" && l.textContent?.includes("credential"),
    )!;
    expect(within(cred).queryByRole("button", { name: "Show evidence" })).toBeNull();
    expect(cred).toHaveTextContent("No event to show");
  });

  it("the Context button counts security findings first: 'Context · 6 security'", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={leaked().run.id} base={BASE} />);
    expect(await screen.findByTestId("context-summary")).toHaveTextContent("· 6 security");
  });

  it("an analysed run with nothing suspicious says so and names the detectors that are on", async () => {
    const calls = stubApi();
    const user = userEvent.setup();
    const clean = fixtureRuns().find((f) => f.run.name === "Fix login session expiry")!;
    renderWithQuery(<RunDetail runId={clean.run.id} base={BASE} />);
    const box = await screen.findByTestId("security-clean");
    expect(box).toHaveTextContent("Nothing suspicious found");
    expect(calls.some((c) => c.includes("/security/detectors"))).toBe(false); // fetched only when opened
    await user.click(within(box).getByText("Detectors that are on"));
    expect(await within(box).findByText("Secret file read")).toBeInTheDocument();
  });

  it("?finding= opens the column, marks the finding and shows its evidence", async () => {
    stubApi();
    const f = leaked();
    const target = f.findings!.items.find((i) => i.detector === "sec.risky_git")!;
    search = `finding=${target.id}`;
    renderWithQuery(<RunDetail runId={f.run.id} base={BASE} />);
    const marked = await vi.waitFor(() => {
      const el = document.querySelector('[data-finding-id][data-focus="true"]');
      expect(el).not.toBeNull();
      return el!;
    });
    expect(marked.getAttribute("data-finding-id")).toBe(target.id);
    expect(screen.getByRole("button", { name: /^Context/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

describe("a finding renders only the API's known fields", () => {
  it("sentinel strings in unexpected fields never reach the page", async () => {
    const f = leaked();
    const base = f.findings!.items.find((i) => i.category === "security")!;
    const evil = {
      ...base,
      path: "SENTINEL-PATH",
      command: "SENTINEL-COMMAND",
      host: "SENTINEL-HOST",
      evidence: {
        ...base.evidence,
        path: "SENTINEL-EVIDENCE-PATH",
        command: "SENTINEL-EVIDENCE-CMD",
        kinds: ["SENTINEL-KIND"],
        rules: ["SENTINEL-RULES"],
      },
    } as unknown as FindingOut;
    const body = { ...f.findings!, items: [evil] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | string) => {
        const url = new URL(typeof input === "string" ? input : input.url);
        return Response.json(url.pathname.endsWith("/findings") ? body : {});
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={client}>
        <SecurityFindings
          runId={f.run.id}
          projectId={f.run.project_id}
          poll={false}
          focusId={null}
          onShowEvent={() => {}}
        />
      </QueryClientProvider>,
    );
    await screen.findByTestId("security-finding");
    expect(container.textContent).not.toMatch(/SENTINEL/);
    for (const s of SECURITY_SENTINELS) expect(container.textContent).not.toContain(s);
  });

  it("a real fixture finding list never shows the content its events hold", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={leaked().run.id} base={BASE} />);
    const group = await screen.findByTestId("security-group");
    await within(group).findAllByTestId("security-finding");
    for (const s of SECURITY_SENTINELS) expect(group.textContent).not.toContain(s);
  });
});
