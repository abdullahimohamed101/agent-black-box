import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReplayTransport } from "@/components/ReplayTransport";
import { failureRetryRun, multiAgentRun } from "@/fixtures/runs";
import { useReplay, type UseReplayOptions } from "@/lib/replay";

const listeners = vi.hoisted(() => new Set<() => void>());
vi.mock("next/navigation", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    listeners.add(cb);
    return () => listeners.delete(cb);
  };
  return {
    useSearchParams: () =>
      new URLSearchParams(
        React.useSyncExternalStore(
          subscribe,
          () => window.location.search,
          () => window.location.search,
        ),
      ),
  };
});

const realReplace = window.history.replaceState.bind(window.history);
beforeEach(() => {
  realReplace(null, "", "/runs/x");
  vi.spyOn(window.history, "replaceState").mockImplementation((s, t, u) => {
    realReplace(s, t, u);
    listeners.forEach((l) => l());
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const f = failureRetryRun();
const ctx = { status: f.run.status, firstErrorId: f.run.first_error!.event_id };
const firstErrIdx = f.events.findIndex((e) => e.event_id === ctx.firstErrorId);

function Harness({ skewed = false, ...opts }: Partial<UseReplayOptions> & { skewed?: boolean }) {
  const replay = useReplay({ events: f.events, ctx, ...opts });
  return (
    <>
      <p data-testid="mode">{replay.mode ? "replay" : "live"}</p>
      {replay.mode && <ReplayTransport replay={replay} skewed={skewed} />}
    </>
  );
}
const open = () => {
  realReplace(null, "", `/runs/x?t=${f.events[0]!.event_id}`); // replay mode from the URL
};
const narrow = () =>
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: /max-width: 40rem/.test(q),
    media: q,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));

describe("ReplayTransport (wide)", () => {
  it("exposes every control with a role and a name", () => {
    open();
    render(<Harness />);
    const bar = screen.getByRole("region", { name: "Replay controls" });
    for (const name of ["Play", "Step back", "Step forward", "Leave replay"])
      expect(within(bar).getByRole("button", { name })).toBeInTheDocument();
    const speeds = within(bar).getByRole("group", { name: "Playback speed" });
    expect(
      within(speeds)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["1x", "2x", "5x"]);
    expect(within(speeds).getByRole("button", { name: "1x" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(speeds).getByRole("button", { name: "5x" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(within(bar).getByRole("checkbox", { name: "Pause at first error" })).toBeChecked();
    expect(within(bar).getByRole("checkbox", { name: "Group steps" })).toBeChecked();
    expect(within(bar).queryByRole("button", { name: "Options" })).toBeNull();
  });

  it("the scrub bar is a real slider with a text value, and a first-error mark that screen readers are told about", () => {
    open();
    render(<Harness />);
    const slider = screen.getByRole("slider", { name: "Replay position" });
    expect(slider.tagName).toBe("INPUT");
    expect(slider).toHaveAttribute("type", "range");
    expect(slider).toHaveAttribute("min", "0");
    expect(slider).toHaveAttribute("max", String(f.events.length - 1));
    expect(slider).toHaveAttribute("aria-valuetext", `event 1 of ${f.events.length}, 0 ms`);
    fireEvent.change(slider, { target: { value: "6" } });
    expect(slider).toHaveValue("6");
    expect(slider.getAttribute("aria-valuetext")).toMatch(
      new RegExp(`^event 7 of ${f.events.length}, \\d`),
    );
    const note = document.getElementById(slider.getAttribute("aria-describedby")!)!;
    expect(note).toHaveTextContent(`First error at event ${firstErrIdx + 1}`);
    const mark = document.querySelector<HTMLElement>(".replay-fault")!;
    expect(mark).toHaveAttribute("aria-hidden", "true");
    expect(mark.style.left).toBe(`${(firstErrIdx / (f.events.length - 1)) * 100}%`);
  });

  it("works by mouse and keyboard: Play/Pause, step, speed, toggles, leave", async () => {
    open();
    const user = userEvent.setup();
    render(<Harness />);
    const play = screen.getByRole("button", { name: "Play" });
    await user.click(play);
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Pause" }));
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Step back" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "5x" }));
    expect(screen.getByRole("button", { name: "5x" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "1x" })).toHaveAttribute("aria-pressed", "false");
    // keyboard: focus Step forward, Enter
    screen.getByRole("button", { name: "Step forward" }).focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("slider")).not.toHaveValue("0");
    await user.click(screen.getByRole("checkbox", { name: "Group steps" }));
    expect(screen.getByRole("checkbox", { name: "Group steps" })).not.toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "Pause at first error" }));
    expect(screen.getByRole("checkbox", { name: "Pause at first error" })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Leave replay" }));
    expect(screen.getByTestId("mode")).toHaveTextContent("live");
    expect(screen.queryByRole("region", { name: "Replay controls" })).toBeNull();
  });

  it("announces the event on a step and a seek, and does not announce a tick", () => {
    vi.useFakeTimers();
    open();
    render(<Harness />);
    const status = () =>
      screen.getAllByRole("status").find((s) => s.classList.contains("sr-only"))!;
    const before = status().textContent;
    act(() => void screen.getByRole("button", { name: "Play" }).click());
    for (let t = 0; t < 1500; t += 25) act(() => void vi.advanceTimersByTime(25));
    expect(Number(screen.getByRole("slider").getAttribute("value"))).toBeGreaterThan(0);
    expect(status().textContent).toBe(before); // playing: silent
    act(() => void screen.getByRole("button", { name: "Pause" }).click());
    act(() => void screen.getByRole("button", { name: "Step forward" }).click());
    expect(status().textContent).toMatch(new RegExp(`^Event \\d+ of ${f.events.length}, `));
    fireEvent.change(screen.getByRole("slider"), { target: { value: "9" } });
    expect(status().textContent).toMatch(new RegExp(`^Event 10 of ${f.events.length}, `));
  });

  it("the gap note is never a live region, and says when playback paused at the first error", () => {
    vi.useFakeTimers();
    open();
    render(<Harness />);
    const gap = screen.getByTestId("replay-gap");
    expect(gap).toHaveAttribute("aria-live", "off");
    act(() => void screen.getByRole("button", { name: "Play" }).click());
    for (let t = 0; t < 30_000; t += 25) act(() => void vi.advanceTimersByTime(25));
    expect(gap).toHaveTextContent("Paused at the first error.");
    expect(screen.getByRole("slider")).toHaveValue(String(firstErrIdx));
  });

  it("shows the elapsed time in mono and the clock-skew note only for a skewed run", () => {
    open();
    const { rerender } = render(<Harness />);
    expect(screen.getByTestId("replay-time")).toHaveClass("replay-time");
    expect(screen.queryByText("Timing across agents is approximate.")).toBeNull();
    rerender(<Harness skewed />);
    expect(screen.getByText("Timing across agents is approximate.")).toBeInTheDocument();
    const css = readFileSync(path.join(__dirname, "../src/app/replay.css"), "utf8");
    expect(css).toMatch(/\.replay-time\s*\{[^}]*var\(--font-mono\)/);
  });

  it("says so, politely, when the playhead in the URL is not in the run", async () => {
    realReplace(null, "", "/runs/x?t=evt_gone");
    const user = userEvent.setup();
    render(<Harness />);
    const note = screen.getByText(/That moment is not in this run/);
    expect(note.closest("[role=status]")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/That moment is not in this run/)).toBeNull();
  });

  it("works for a multi-agent run's events too (no content, only kinds)", () => {
    const m = multiAgentRun();
    realReplace(null, "", `/runs/x?t=${m.events[0]!.event_id}`);
    function Multi() {
      const replay = useReplay({ events: m.events, ctx: { status: m.run.status } });
      return <ReplayTransport replay={replay} skewed />;
    }
    render(<Multi />);
    expect(screen.getByRole("slider")).toHaveAttribute("max", String(m.events.length - 1));
  });
});

describe("ReplayTransport (phone width)", () => {
  it("keeps play, step and the scrub bar, and moves speed, the toggles and Leave into a menu", async () => {
    narrow();
    open();
    const user = userEvent.setup();
    render(<Harness />);
    const bar = screen.getByRole("region", { name: "Replay controls" });
    expect(within(bar).getByRole("button", { name: "Play" })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Step back" })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Step forward" })).toBeInTheDocument();
    expect(within(bar).getByRole("slider", { name: "Replay position" })).toBeInTheDocument();
    for (const gone of ["Leave replay", "1x"])
      expect(within(bar).queryByRole("button", { name: gone })).toBeNull();
    expect(within(bar).queryByRole("checkbox")).toBeNull();

    const menu = within(bar).getByRole("button", { name: "Options" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    await user.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    const panel = document.getElementById(menu.getAttribute("aria-controls")!)!;
    expect(within(panel).getByRole("button", { name: "2x" })).toBeInTheDocument();
    expect(within(panel).getByRole("checkbox", { name: "Group steps" })).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "2x" }));
    expect(within(panel).getByRole("button", { name: "2x" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("Esc closes the menu and returns focus to its button, without leaving replay", async () => {
    narrow();
    open();
    const user = userEvent.setup();
    render(<Harness />);
    const menu = screen.getByRole("button", { name: "Options" });
    await user.click(menu);
    await user.click(screen.getByRole("checkbox", { name: "Group steps" }));
    expect(screen.getByRole("checkbox", { name: "Group steps" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveFocus();
    expect(screen.getByTestId("mode")).toHaveTextContent("replay");
    await user.keyboard("{Escape}"); // a second Esc is replay's own
    expect(screen.getByTestId("mode")).toHaveTextContent("live");
  });

  it("a tap outside closes the menu", async () => {
    narrow();
    open();
    const user = userEvent.setup();
    render(<Harness />);
    const menu = screen.getByRole("button", { name: "Options" });
    await user.click(menu);
    await user.click(document.body);
    expect(menu).toHaveAttribute("aria-expanded", "false");
  });

  it("the stylesheet sizes touch targets at 44 px and uses only tokens", () => {
    const css = readFileSync(path.join(__dirname, "../src/app/replay.css"), "utf8").replace(
      /\/\*[\s\S]*?\*\//g,
      "",
    );
    expect(css).toMatch(
      /@media \(pointer: coarse\), \(max-width: 40rem\)\s*\{[^@]*min-height: 2\.75rem/,
    );
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(|transition|animation/i);
  });
});

describe("replay hydration", () => {
  it("the first client render equals the server render, with or without ?t=, whatever the motion setting", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: true,
      media: q,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }));
    for (const url of ["/runs/x", `/runs/x?t=${f.events[3]!.event_id}`]) {
      realReplace(null, "", url);
      const html = renderToString(<Harness />);
      const host = document.body.appendChild(document.createElement("div"));
      host.innerHTML = html;
      const errors: unknown[] = [];
      await act(async () => {
        hydrateRoot(host, <Harness />, { onRecoverableError: (e) => errors.push(e) });
      });
      expect(errors).toEqual([]);
      host.remove();
    }
  });
});
