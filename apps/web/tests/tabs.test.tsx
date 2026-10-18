import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Tabs, tabIds } from "@/components/Tabs";

const ITEMS = [
  { id: "a", label: "Timeline", count: "12" },
  { id: "b", label: "Waterfall" },
  { id: "c", label: "Evidence", count: "4" },
] as const;

function Host({ onChange }: { onChange?: (v: string) => void }) {
  const [v, setV] = useState<"a" | "b" | "c">("a");
  return (
    <>
      <Tabs
        label="Views"
        idBase="t"
        items={ITEMS}
        value={v}
        onChange={(n) => {
          setV(n);
          onChange?.(n);
        }}
      />
      <div role="tabpanel" {...tabIds("t", v)}>
        panel {v}
      </div>
    </>
  );
}

describe("Tabs (the one tab component)", () => {
  it("is a tablist of tab buttons; the selected one is the only tab stop and names the panel", () => {
    render(<Host />);
    const list = screen.getByRole("tablist", { name: "Views" });
    expect(list).toHaveAttribute("aria-orientation", "horizontal");
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false"]);
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1", "-1"]);
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("id", tabs[0]!.getAttribute("aria-controls"));
    expect(panel).toHaveAttribute("aria-labelledby", tabs[0]!.id);
  });

  it("counts are decoration: the accessible name of a tab is its plain label", () => {
    render(<Host />);
    expect(screen.getByRole("tab", { name: "Timeline" })).toHaveTextContent("12");
    expect(screen.getByRole("tab", { name: "Evidence" })).toBeInTheDocument();
  });

  it("arrow keys move and select, Home and End jump, and the ends wrap", async () => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(<Host onChange={change} />);
    screen.getByRole("tab", { name: "Timeline" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Waterfall" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Waterfall" })).toHaveFocus();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Evidence" })).toHaveFocus();
    await user.keyboard("{ArrowRight}"); // wraps to the first
    expect(screen.getByRole("tab", { name: "Timeline" })).toHaveFocus();
    await user.keyboard("{ArrowLeft}"); // and back to the last
    expect(screen.getByRole("tab", { name: "Evidence" })).toHaveFocus();
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Timeline" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel a");
    expect(change.mock.calls.map((c) => c[0])).toEqual(["b", "c", "a", "c", "a"]);
  });

  it("a click selects, and Tab leaves the list instead of walking its tabs", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Host />
        <button type="button">after</button>
      </>,
    );
    await user.click(screen.getByRole("tab", { name: "Evidence" }));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel c");
    await user.tab();
    expect(screen.getByRole("button", { name: "after" })).toHaveFocus();
  });

  it("other keys are left alone", async () => {
    const user = userEvent.setup();
    render(<Host />);
    screen.getByRole("tab", { name: "Timeline" }).focus();
    await user.keyboard("j");
    expect(screen.getByRole("tab", { name: "Timeline" })).toHaveAttribute("aria-selected", "true");
  });
});
