import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_FILTERS, RunsList, type ListFilters } from "@/components/RunsList";
import { BASE, renderWithQuery, stubApi } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

function Harness() {
  const [f, setF] = useState<ListFilters>(DEFAULT_FILTERS);
  return (
    <RunsList base={BASE} filters={f} onFilters={setF} now={Date.parse("2026-10-08T00:00:00Z")} />
  );
}

describe("the compare bar's hint", () => {
  it("asks for one more run only when exactly one is picked", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    const bar = await screen.findByTestId("compare-bar");
    expect(bar).toHaveTextContent("Tick 2 runs to compare them.");
    expect(bar).not.toHaveTextContent("Pick one more run.");
    const boxes = screen.getAllByRole("checkbox", { name: /^Compare / });
    await user.click(boxes[0]!);
    expect(bar).toHaveTextContent("Pick one more run.");
    await user.click(boxes[1]!);
    expect(bar).not.toHaveTextContent("Pick one more run.");
  });
});
