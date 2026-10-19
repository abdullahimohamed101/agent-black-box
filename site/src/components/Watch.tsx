import { existsSync } from "node:fs";
import path from "node:path";
import { publicUrl } from "../config";
import { TeaserVideo, type VideoSource } from "./TeaserVideo";

const DIR = path.join(process.cwd(), "public/assets/video");
const has = (f: string) => existsSync(path.join(DIR, f));
const url = (f: string) => publicUrl(`assets/video/${f}`);

/**
 * Section 5 (#watch), gated on G1: it renders only when the teaser files exist in public/assets/video at build time
 * (poster.webp and at least one of teaser-1080p-av1.mp4, teaser-1080p.webm, teaser-1080p.mp4; teaser.vtt and demo-1080p.mp4 are
 * optional). Until then the section is not in the export, so the page has no empty box and no broken link.
 */
export function Watch() {
  const sources: VideoSource[] = [];
  if (has("teaser-1080p-av1.mp4")) sources.push({ src: url("teaser-1080p-av1.mp4"), type: 'video/mp4; codecs="av01.0.08M.08"' });
  if (has("teaser-1080p.webm")) sources.push({ src: url("teaser-1080p.webm"), type: "video/webm" });
  if (has("teaser-1080p.mp4")) sources.push({ src: url("teaser-1080p.mp4"), type: "video/mp4" });
  if (!sources.length || !has("poster.webp")) return null;
  return (
    <section className="band" id="watch" aria-labelledby="watch-h">
      <div className="wrap">
        <p className="eyebrow">Watch</p>
        <div className="watch-head">
          <h2 className="h2" id="watch-h">A failed run, in eighteen seconds.</h2>
          {has("demo-1080p.mp4") ? (
            <a href={url("demo-1080p.mp4")} className="watch-full">Full cut, about ninety seconds</a>
          ) : null}
        </div>
        <TeaserVideo poster={url("poster.webp")} sources={sources} captions={has("teaser.vtt") ? url("teaser.vtt") : undefined} width={1920} height={1080} />
        <p className="fine">Captions only. The demo data, recorded by script.</p>
      </div>
    </section>
  );
}
