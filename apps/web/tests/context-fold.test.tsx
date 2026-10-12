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
  await user.click(await screen.findByRole("button", { name: "Show it" }));
  await screen.findByRole("dialog");
  return container.querySelector(".work")!;
}

describe("the context column folds only when the pane lacks width", () => {
  it("stays open at 1440px (pane 512px >= 480px) when the inspector opens", async () => {
    expect(await openInspector(1440)).toHaveAttribute("data-context", "open");
  });
  it("folds at 1280px where the pane would be 352px", async () => {
    expect(await openInspector(1280)).toHaveAttribute("data-context", "closed");
  });
});
