import type { Page } from "@playwright/test";

const art = (k: number) => `artifact://art_${String(k).padStart(26, "0")}`;

/**
 * The fixture data source has no artifact store, so its coding run captures no artifacts. For the Evidence view's
 * screenshots and checks this gives the events of any run a diff per edit and stdout per failed command, by rewriting
 * the events response in the browser. Only attributes are added; no content is ever served for them.
 */
export async function withArtifacts(page: Page) {
  await page.route(/\/v1\/runs\/[^/]+\/events(\?|$)/, async (route) => {
    const res = await route.fetch();
    if (!res.ok()) return route.fulfill({ response: res });
    const body = await res.json();
    let k = 0;
    for (const e of body.items ?? []) {
      if (e.event_type === "file.modified") e.attributes["diff.artifact"] = art(++k);
      if (e.event_type === "shell.command.failed") {
        e.attributes["shell.stdout_artifact"] = art(++k);
        e.attributes["shell.stdout_bytes"] = 2048;
        e.attributes["shell.stderr_artifact"] = art(++k);
        e.attributes["shell.stderr_bytes"] = 311;
      }
    }
    return route.fulfill({ response: res, json: body });
  });
}
