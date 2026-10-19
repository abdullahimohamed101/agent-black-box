// C4 / brief section 7: every animation is opt-in. Under prefers-reduced-motion: reduce nothing animates and the final state is
// complete; with no preference the fault line really draws (its transform changes between load and one second later).
export default async function motion({ srv, browser, rep }) {
  const names = async (page, sel, pseudo = null) => page.evaluate(([s, ps]) => {
    const el = document.querySelector(s);
    if (!el) return null;
    const cs = getComputedStyle(el, ps);
    return { animation: cs.animationName, transition: cs.transitionProperty + "/" + cs.transitionDuration, transform: cs.transform };
  }, [sel, pseudo]);

  const reduced = await browser.newContext({ reducedMotion: "reduce", viewport: { width: 1440, height: 900 } });
  const rp = await reduced.newPage();
  await rp.goto(srv.url, { waitUntil: "networkidle" });
  const targets = [[".first-error", "::before"], [".first-error", "::after"], [".insp-body"], [".step"], [".gallery"]];
  for (const [sel, pseudo] of targets) {
    const r = await names(rp, sel, pseudo ?? null);
    if (r === null) { if (sel !== ".gallery") rep.fail("motion", `${sel} not found`); continue; }
    if (r.animation !== "none") rep.fail("motion", `reduced motion: ${sel}${pseudo ?? ""} still has animation ${r.animation}`);
  }
  const line = await names(rp, ".first-error", "::before");
  if (line && line.transform !== "none" && line.transform !== "matrix(1, 0, 0, 1, 0, 0)") rep.fail("motion", `reduced motion: the fault line is not in its final state (${line.transform})`);
  const videoPlaying = await rp.evaluate(() => [...document.querySelectorAll("video")].some((v) => !v.paused));
  if (videoPlaying) rep.fail("motion", "a video plays under reduced motion");
  const videos = await rp.evaluate(() => document.querySelectorAll("video").length);
  const autoplayAttr = await rp.evaluate(() => document.querySelectorAll("video[autoplay]").length);
  if (autoplayAttr) rep.fail("motion", "a video carries the autoplay attribute (the island starts the loop, and only when motion is allowed)");
  if (videos && !(await rp.locator("button.play").count())) rep.fail("motion", "reduced motion: the teaser has no play control");
  rep.fact(`reduced motion: no animation on the fault line, inspector, steps or gallery; ${videos} video(s) in the DOM, none playing${videos ? ", play control shown" : ""}`);
  await reduced.close();

  const live = await browser.newContext({ reducedMotion: "no-preference", viewport: { width: 1440, height: 900 } });
  const lp = await live.newPage();
  await lp.goto(srv.url, { waitUntil: "load" });
  const t0 = (await names(lp, ".first-error", "::before")).transform;
  await lp.waitForTimeout(1100);
  const t1 = (await names(lp, ".first-error", "::before")).transform;
  if (t0 === t1) rep.fail("motion", `the fault line did not animate (${t0} then ${t1})`);
  if (t1 !== "none" && t1 !== "matrix(1, 0, 0, 1, 0, 0)") rep.fail("motion", `the fault line did not finish drawing (${t1})`);
  rep.fact(`no-preference: fault line transform ${t0} at load, ${t1} after 1.1 s`);
  await live.close();
}
