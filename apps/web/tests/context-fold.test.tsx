import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import { failureRetryRun } from "@/fixtures/runs";
import { BASE, renderWithQuery, stubApi } from "./helpers";

vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs/x",
  useSearchParams: () => new URLSearchParams(""),
}));
afterEach(() => vi.unstubAllGlobals());

/** A window `px` wide: a max-width query matches when it is at least as wide as the viewport. */
function stubWidth(px: number) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => {
      const m = /max-width:\s*([\d.]+)(rem|px)/.exec(query);
      const limit = m ? Number(m[1]) * (m[2] === "rem" ? 16 : 1) : 0;
      return {
        matches: px <= limit,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      };
    }),
  );
}

async function openInspector(width: number) {
  stubApi();
  stubWidth(width);
  const user = userEvent.setup();
  const { run } = failureRetryRun();
  const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
  await screen.findByRole("listbox", { name: /Run timeline/ });
  await user.click(screen.getByRole("button", { name: /^Context/ })); // Phase 22: closed until asked for
  await user.click(await screen.findByRole("button", { name: "Show it" }));
  await screen.findByRole("dialog");
  return container.querySelector(".work")!;
}

describe("an open context column folds only when the window cannot hold it", () => {
  it("stays open at 1440px with the full-width inspector", async () => {
    const work = await openInspector(1440);
    expect(work).toHaveAttribute("data-context", "open");
    expect(work).not.toHaveAttribute("data-compact");
  });
  it("stays open at 1280px (KI-126): the inspector narrows instead", async () => {
    const work = await openInspector(1280);
    expect(work).toHaveAttribute("data-context", "open");
    expect(work).toHaveAttribute("data-compact");
  });
  it("folds below 1280px, where rail, column, inspector and pane cannot all fit", async () => {
    expect(await openInspector(1100)).toHaveAttribute("data-context", "closed");
  });
});
