import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EvidenceList } from "@/components/EvidenceList";
import type { EvidenceRow } from "@/lib/evidence";

const rows = (n: number): EvidenceRow[] =>
  Array.from({ length: n }, (_, i) => ({
    key: `ev${i}:diff`,
    kind: "diff",
    eventId: `ev${i}`,
    eventType: "file.modified",
    sequence: i,
    occurredAt: "2026-01-01T00:00:00Z",
    what: `src/file${i}.ts`,
    bytes: 10,
    artifactId: `art_${i}`,
  }));

describe("Evidence list is virtualized", () => {
  it("keeps the DOM bounded for thousands of rows and still announces the full count", () => {
    render(<EvidenceList rows={rows(5000)} t0={0} selectedEventId={null} onOpen={() => {}} />);
    const table = screen.getByRole("table", { name: "Evidence" });
    const dom = within(table).getAllByRole("row");
    expect(dom.length).toBeLessThan(80);
    expect(table).toHaveAttribute("aria-rowcount", "5001");
    expect(dom[1]).toHaveAttribute("aria-rowindex", "2");
    // the scroll height is that of all rows: spacer rows are hidden from assistive tech
    expect(table.querySelectorAll('tr[aria-hidden="true"]').length).toBeGreaterThan(0);
  });

  it("the region stays a keyboard tab stop and Open still reports the event", async () => {
    const onOpen = vi.fn();
    render(<EvidenceList rows={rows(3000)} t0={0} selectedEventId={null} onOpen={onOpen} />);
    const region = screen.getByRole("region", { name: "Captured artifacts" });
    expect(region).toHaveAttribute("tabindex", "0");
    await userEvent.setup().click(screen.getAllByRole("button", { name: /^Open diff/ })[2]!);
    expect(onOpen).toHaveBeenCalledWith("ev2");
  });
});
