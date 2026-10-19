import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../fixtures/migrate-billing-schema.json";
import { FaultTrace } from "../src/components/FaultTrace";
import { buildTrace, type Fixture } from "../src/lib/trace";

const model = buildTrace(fixture as unknown as Fixture);
const selected = () => screen.getByRole("option", { selected: true });

describe("buildTrace", () => {
  it("shows nine of the fifteen events and says where it broke", () => {
    expect(model.rows).toHaveLength(9);
    expect(model.total).toBe(15);
    expect(model.headline).toBe("Failed at step 7 after 6.2 s: run_migration, ConnectionTimeout.");
    expect(model.rows[model.firstErrorIndex]?.type).toBe("tool.call.failed");
    expect(model.rows.map((r) => r.t)).toEqual(["+0ms", "+100ms", "+500ms", "+2.9s", "+3.1s", "+6.2s", "+7.2s", "+10.3s", "+14.4s"]);
  });
  it("has exactly three failed words, one retry and a first error", () => {
    expect(model.rows.filter((r) => r.status === "error")).toHaveLength(3);
    expect(model.rows.filter((r) => r.status === "retry")).toHaveLength(1);
  });
});

describe("FaultTrace", () => {
  it("renders every row and the first error's inspector on the server, with no effect needed", () => {
    const html = renderToString(<FaultTrace model={model} />);
    for (const r of model.rows) expect(html).toContain(r.type);
    expect(html).toContain('aria-selected="true"');
    expect(html).toContain("run_migration failed: ConnectionTimeout");
    expect(html).toContain("ConnectionTimeout");
  });

  it("starts on the first error, which is the only tab stop", () => {
    render(<FaultTrace model={model} />);
    expect(selected()).toHaveTextContent("First error, step 7");
    const stops = screen.getAllByRole("option").filter((o) => o.tabIndex === 0);
    expect(stops).toEqual([selected()]);
    expect(screen.getByLabelText("Selected event")).toHaveTextContent("What went wrong");
  });

  it("j and k move the selection and the inspector follows; e returns to the first error", async () => {
    const user = userEvent.setup();
    render(<FaultTrace model={model} />);
    selected().focus();
    await user.keyboard("j");
    expect(selected()).toHaveTextContent("retry.attempted");
    expect(screen.getByLabelText("Selected event")).toHaveTextContent("Retry #1 after ConnectionTimeout");
    await user.keyboard("k");
    await user.keyboard("k");
    expect(selected()).toHaveTextContent("tool.call.started");
    await user.keyboard("e");
    expect(selected()).toHaveTextContent("tool.call.failed");
    expect(selected()).toHaveTextContent("First error, step 7");
    await user.keyboard("{End}");
    expect(selected()).toHaveTextContent("run.failed");
    await user.keyboard("j");
    expect(selected()).toHaveTextContent("run.failed");
    await user.keyboard("{Home}");
    expect(selected()).toHaveTextContent("run.started");
  });

  it("Enter and Space keep the selection (they open the inspector, already showing it)", async () => {
    const user = userEvent.setup();
    Element.prototype.scrollIntoView = () => {};
    render(<FaultTrace model={model} />);
    selected().focus();
    await user.keyboard("{Enter}");
    await user.keyboard(" ");
    expect(selected()).toHaveTextContent("tool.call.failed");
  });

  it("announces the inspector politely and labels it", () => {
    render(<FaultTrace model={model} />);
    const insp = screen.getByLabelText("Selected event");
    expect(insp).toHaveAttribute("aria-live", "polite");
    expect(within(insp).getByText("tool.call.failed")).toBeInTheDocument();
  });

  it("selects on click and on mouse hover only", async () => {
    const user = userEvent.setup();
    render(<FaultTrace model={model} />);
    await user.click(screen.getAllByRole("option")[0]!);
    expect(selected()).toHaveTextContent("run.started");
    await user.hover(screen.getAllByRole("option")[4]!);
    expect(selected()).toHaveTextContent("tool.call.started");
  });
});
