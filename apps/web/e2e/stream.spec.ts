import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { spawn } from "node:child_process";
import net from "node:net";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { signIn, workspace } from "./session";

// Runs only through scripts/stream-e2e.sh: a real API + worker, the built web server on :3103, and a driver process
// (the Python SDK, or plain HTTP) that writes the run while the browser is already watching it.
const writeKey = process.env.E2E_STREAM_WRITE_KEY;
const apiUrl = process.env.E2E_STREAM_API_URL;
test.skip(!writeKey || !apiUrl, "needs scripts/stream-e2e.sh");
test.use({ baseURL: "http://localhost:3103" });
test.beforeEach(async ({ context, baseURL }) => signIn(context, baseURL!));
test.describe.configure({ mode: "serial" }); // one writer at a time keeps the latency numbers honest
const SHOTS = "../../docs/screenshots/phase-5";

type Driver = {
  runId: string;
  go: () => void;
  acks: { event_id: string; index: number; ack_ms: number }[];
  done: Promise<void>;
  kill: () => void;
};

async function startDriver(...args: string[]): Promise<Driver> {
  const child = spawn(
    "uv",
    [
      "run",
      "--project",
      "../../packages/sdk-python",
      "python",
      "../../scripts/stream_driver.py",
      ...args,
    ],
    { env: { ...process.env, DRIVER_API_URL: apiUrl!, DRIVER_WRITE_KEY: writeKey! } },
  );
  const acks: Driver["acks"] = [];
  let stderr = "";
  child.stderr.on("data", (d) => (stderr += String(d)));
  let resolveRun!: (id: string) => void;
  const runIdPromise = new Promise<string>((r) => (resolveRun = r));
  let resolveDone!: () => void;
  let rejectDone!: (e: Error) => void;
  const done = new Promise<void>((res, rej) => ((resolveDone = res), (rejectDone = rej)));
  let buffer = "";
  child.stdout.on("data", (chunk) => {
    buffer += String(chunk);
    for (let nl = buffer.indexOf("\n"); nl >= 0; nl = buffer.indexOf("\n")) {
      const line = buffer.slice(0, nl);
      buffer = buffer.slice(nl + 1);
      if (!line.startsWith("{")) continue;
      const msg = JSON.parse(line) as Record<string, unknown>;
      if (typeof msg.run_id === "string") resolveRun(msg.run_id);
      else if (typeof msg.ack_ms === "number") acks.push(msg as unknown as Driver["acks"][number]);
      else if (msg.done) resolveDone();
    }
  });
  child.on("exit", (code) => {
    if (code !== 0) rejectDone(new Error(`driver exited ${code}: ${stderr.slice(-500)}`));
  });
  const runId = await Promise.race([
    runIdPromise,
    done.then(() => Promise.reject(new Error("driver finished without a run id"))),
  ]);
  return {
    runId,
    go: () => child.stdin.write("go\n"),
    acks,
    done,
    kill: () => child.kill(),
  };
}

const runUrl = (id: string) => `/w/${workspace()}/projects/all/runs/${id}`;
const shown = async (page: Page) => {
  const text = (await page.getByTestId("progress").textContent()) ?? "";
  const m = /(\d+) of (\d+) events shown/.exec(text);
  return m ? Number(m[2]) : -1;
};

test("the SDK example agent's trace fills in live, with no refresh, and ends cleanly", async ({
  page,
}) => {
  const driver = await startDriver("sdk");
  try {
    let loads = 0;
    page.on("load", () => loads++);
    await page.goto(runUrl(driver.runId));
    await expect(page.getByTestId("live-status")).toContainText("Live");
    await expect(page.getByTestId("headline")).toContainText("In progress");
    const before = await shown(page);
    driver.go();
    await expect(page.getByTestId("live-status")).toContainText("Running tool search_code");
    await expect(page.getByTestId("live-status")).toContainText("Calling model model-x");
    await page.screenshot({ path: `${SHOTS}/live-running.png`, fullPage: true });
    await expect.poll(() => shown(page)).toBeGreaterThan(before);
    await expect(page.getByTestId("headline")).toContainText("Succeeded", { timeout: 20_000 });
    await expect(page.getByTestId("live-status")).toHaveCount(0); // run_end: the bar is gone
    await expect(page.getByTestId("progress")).toContainText("events shown");
    expect(loads).toBe(1); // the page was never reloaded
    const types = await page.locator(".tl-type").allTextContents();
    expect(types).toEqual([
      "run.started",
      "tool.call.started",
      "tool.call.completed",
      "llm.request.started",
      "llm.request.completed",
      "tool.call.started",
      "tool.call.completed",
      "run.completed",
    ]);
    await page.screenshot({ path: `${SHOTS}/live-finished.png`, fullPage: true });
    await driver.done;
  } finally {
    driver.kill();
  }
});

test("a live run page has no serious accessibility violations and no console errors", async ({
  page,
}) => {
  const driver = await startDriver("http", "12", "0.25");
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  try {
    await page.goto(runUrl(driver.runId));
    await expect(page.getByTestId("live-status")).toContainText("Live");
    driver.go();
    await expect.poll(() => shown(page)).toBeGreaterThan(3);
    const bad = (
      await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()
    ).violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad).toEqual([]);
    await driver.done;
    expect(errors).toEqual([]);
  } finally {
    driver.kill();
  }
});

test("a browser refresh mid-run resumes without losing or duplicating events", async ({ page }) => {
  const driver = await startDriver("http", "30", "0.2");
  try {
    await page.goto(runUrl(driver.runId));
    await expect(page.getByTestId("live-status")).toContainText("Live");
    driver.go();
    await expect.poll(() => shown(page)).toBeGreaterThanOrEqual(8);
    await page.reload();
    await expect(page.getByTestId("live-status")).toContainText(/Live|Connecting/);
    await driver.done;
    // 1 run.started + 30 steps + 1 run.completed, each exactly once
    await expect.poll(() => shown(page), { timeout: 15_000 }).toBe(32);
    const ids = await page.locator("[role=option]").evaluateAll((els) => els.map((e) => e.id));
    expect(new Set(ids).size).toBe(ids.length);
  } finally {
    driver.kill();
  }
});

/** A TCP pass-through to the web server that can cut every connection and refuse new ones, like a flaky network. */
async function faultProxy(targetPort: number) {
  let down = false;
  const sockets = new Set<net.Socket>();
  const server = net.createServer((client) => {
    if (down) return void client.destroy();
    const upstream = net.connect(targetPort, "127.0.0.1");
    for (const sock of [client, upstream]) {
      sockets.add(sock);
      sock.on("close", () => sockets.delete(sock));
      sock.on("error", () => sock.destroy());
    }
    client.pipe(upstream);
    upstream.pipe(client);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    port: (server.address() as net.AddressInfo).port,
    cut: () => {
      down = true;
      for (const sock of sockets) sock.destroy();
    },
    restore: () => void (down = false),
    close: () => server.close(),
  };
}

test("a dropped connection is reported as partial data and recovers with every event present", async ({
  page,
  context,
}) => {
  const proxy = await faultProxy(3103);
  await signIn(context, `http://127.0.0.1:${proxy.port}`); // another host: the cookie does not follow
  const driver = await startDriver("http", "24", "0.25");
  try {
    await page.goto(`http://127.0.0.1:${proxy.port}${runUrl(driver.runId)}`);
    await expect(page.getByTestId("live-status")).toContainText("Live");
    driver.go();
    await expect.poll(() => shown(page)).toBeGreaterThanOrEqual(4);
    proxy.cut(); // the network goes away while the agent keeps working
    await expect(page.getByTestId("partial-data")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("live-status")).toContainText("Reconnecting");
    await page.screenshot({ path: `${SHOTS}/live-partial-data.png`, fullPage: true });
    await page.waitForTimeout(2500); // events keep arriving at the API meanwhile
    proxy.restore();
    await driver.done;
    await expect.poll(() => shown(page), { timeout: 30_000 }).toBe(26); // 1 + 24 + run.completed
    await expect(page.getByTestId("partial-data")).toHaveCount(0);
    const ids = await page.locator("[role=option]").evaluateAll((els) => els.map((e) => e.id));
    expect(new Set(ids).size).toBe(ids.length);
  } finally {
    proxy.close();
    driver.kill();
  }
});

test("accept-to-display latency: p95 under one second", async ({ page }) => {
  const COUNT = 60;
  const driver = await startDriver("http", String(COUNT), "0.2");
  try {
    await page.goto(runUrl(driver.runId));
    await expect(page.getByTestId("live-status")).toContainText("Live");
    // when did each count of events first appear on screen? (the progress line shows the model's size)
    await page.evaluate(() => {
      const w = window as unknown as { __seen: Record<number, number> };
      w.__seen = {};
      const el = document.querySelector('[data-testid="progress"]')!;
      const read = () => {
        const m = /(\d+) of (\d+) events shown/.exec(el.textContent ?? "");
        if (m) {
          const total = Number(m[2]);
          for (let k = 1; k <= total; k++) w.__seen[k] ??= Date.now();
        }
      };
      new MutationObserver(read).observe(el, {
        childList: true,
        characterData: true,
        subtree: true,
      });
      read();
    });
    driver.go();
    await driver.done;
    await expect.poll(() => shown(page), { timeout: 15_000 }).toBe(COUNT + 2);
    const seen = await page.evaluate(
      () => (window as unknown as { __seen: Record<number, number> }).__seen,
    );
    // event i of the driver is the (i + 1)-th event of the run (run.started is first)
    const latencies = driver.acks.map((a) => seen[a.index + 1]! - a.ack_ms).sort((x, y) => x - y);
    expect(latencies).toHaveLength(COUNT);
    const q = (p: number) =>
      latencies[Math.min(latencies.length - 1, Math.ceil(p * latencies.length) - 1)]!;
    const result = {
      events: COUNT,
      p50_ms: q(0.5),
      p95_ms: q(0.95),
      max_ms: latencies.at(-1),
      min_ms: latencies[0],
    };
    console.log("STREAM LATENCY", JSON.stringify(result));
    const out = process.env.STREAM_LATENCY_OUT;
    if (out) {
      mkdirSync(path.dirname(out), { recursive: true });
      writeFileSync(out, JSON.stringify(result, null, 2));
    }
    expect(q(0.95)).toBeLessThan(1000);
  } finally {
    driver.kill();
  }
});
