import { render, screen } from "@testing-library/react";
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
});
