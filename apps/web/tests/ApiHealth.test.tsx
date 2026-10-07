import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiHealth } from "@/components/ApiHealth";

afterEach(() => vi.unstubAllGlobals());

describe("ApiHealth", () => {
  it("shows loading, then ready", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve(
        new Response(JSON.stringify({ status: "ok", version: "0.0.0", database: "ok" })),
      ),
    );
    render(<ApiHealth />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking API");
    expect(await screen.findByText(/Ready \(API v0\.0\.0/)).toBeInTheDocument();
  });

  it("shows an error state with the request id", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve(
        new Response(
          JSON.stringify({ error: { message: "Database is not reachable.", request_id: "req_9" } }),
          { status: 503 },
        ),
      ),
    );
    render(<ApiHealth />);
    expect(await screen.findByText(/Not ready: Database is not reachable/)).toBeInTheDocument();
    expect(screen.getByText(/req_9/)).toBeInTheDocument();
  });

  it("shows an unreachable state", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new Error("offline")));
    render(<ApiHealth />);
    expect(await screen.findByText(/API unreachable: offline/)).toBeInTheDocument();
  });

  it("ignores a stale response from a superseded check", async () => {
    const resolvers: Array<(r: Response) => void> = [];
    vi.stubGlobal("fetch", () => new Promise<Response>((resolve) => resolvers.push(resolve)));
    render(<ApiHealth />);
    fireEvent.click(screen.getByRole("button", { name: "Re-check" }));
    const ok = (version: string) =>
      new Response(JSON.stringify({ status: "ok", version, database: "ok" }));
    // The second (latest) check answers first; the first, older answer arrives afterwards.
    resolvers[1]!(ok("new"));
    expect(await screen.findByText(/API vnew/)).toBeInTheDocument();
    resolvers[0]!(ok("old"));
    await Promise.resolve();
    expect(screen.queryByText(/API vold/)).not.toBeInTheDocument();
    expect(screen.getByText(/API vnew/)).toBeInTheDocument();
  });
});
