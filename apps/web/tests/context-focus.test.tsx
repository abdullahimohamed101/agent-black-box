import { screen, waitFor } from "@testing-library/react";
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

describe("expanding the folded context strip", () => {
  it("moves focus to the context column, never to <body>", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const strip = await screen.findByRole("button", { name: "Show run context" });
    expect(strip).toHaveAttribute("aria-expanded", "false");
    strip.focus();
    await user.keyboard("{Enter}");
    const column = screen.getByRole("complementary", { name: "Run context" });
    await waitFor(() => expect(column).toHaveFocus());
    expect(document.activeElement).not.toBe(document.body);
  });
});
