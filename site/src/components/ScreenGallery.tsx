"use client";

import { useRef, useState, useSyncExternalStore, type CSSProperties, type KeyboardEvent } from "react";
import type { GalleryPanel } from "../lib/gallery";

const noop = () => () => {};
const useHydrated = () => useSyncExternalStore(noop, () => true, () => false);

type Vars = CSSProperties & Record<`--${string}`, string | number>;

/**
 * Section 6 (#screens). Without script every panel is in the page, stacked, and the tab list is a list of in-page links; once
 * hydrated it becomes a real tablist (arrow keys, Home/End) showing one panel. Changing panels does not animate.
 * Each image is a <picture> per colour scheme (WebP first, PNG fallback) in a box with a fixed aspect ratio, so nothing shifts.
 * Callouts are positioned in SOURCE pixels: --px is one source pixel in container units, so they land exactly at every width.
 */
export function ScreenGallery({ panels }: { panels: GalleryPanel[] }) {
  const hydrated = useHydrated();
  const [active, setActive] = useState(0);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const move = (i: number) => {
    const n = (i + panels.length) % panels.length;
    setActive(n);
    tabRefs.current[n]?.focus();
  };
  const onKey = (e: KeyboardEvent) => {
    const k = e.key;
    if (k === "ArrowRight" || k === "ArrowDown") move(active + 1);
    else if (k === "ArrowLeft" || k === "ArrowUp") move(active - 1);
    else if (k === "Home") move(0);
    else if (k === "End") move(panels.length - 1);
    else return;
    e.preventDefault();
  };

  return (
    <div className="gallery">
      <div className="tabs" role={hydrated ? "tablist" : undefined} aria-label={hydrated ? "Screens" : undefined} onKeyDown={hydrated ? onKey : undefined}>
        {panels.map((p, i) =>
          hydrated ? (
            <button
              key={p.id}
              ref={(el) => {
                tabRefs.current[i] = el;
              }}
              type="button"
              role="tab"
              id={`tab-${p.id}`}
              aria-selected={i === active}
              aria-controls={`panel-${p.id}`}
              tabIndex={i === active ? 0 : -1}
              className="tab"
              onClick={() => setActive(i)}
            >
              <b>{p.tab}</b>
              <span>{p.sub}</span>
            </button>
          ) : (
            <a key={p.id} className="tab" href={`#panel-${p.id}`}>
              <b>{p.tab}</b>
              <span>{p.sub}</span>
            </a>
          ),
        )}
      </div>
      <div className="panels">
        {panels.map((p, i) => (
          <div
            key={p.id}
            className="panel"
            id={`panel-${p.id}`}
            role={hydrated ? "tabpanel" : undefined}
            aria-labelledby={hydrated ? `tab-${p.id}` : undefined}
            hidden={hydrated && i !== active}
            data-confirm={p.standIn ? "[CONFIRM AFTER STEP 12: this image is a docs/screenshots/pass-2 stand-in, not the release set]" : undefined}
          >
            <figure>
            <div className="shot" style={{ "--zoom-d": p.zoom, "--ox": p.offsetX, "--oy": p.offsetY } as Vars}>
              <picture>
                <source srcSet={p.dark.webp} type="image/webp" media="(prefers-color-scheme: dark)" />
                <source srcSet={p.dark.png} media="(prefers-color-scheme: dark)" />
                <source srcSet={p.light.webp} type="image/webp" />
                <img src={p.light.png} width={p.light.w} height={p.light.h} alt={p.alt} loading="lazy" decoding="async" />
              </picture>
              {p.callouts.map((c, n) => (
                <span key={n} className="co" style={{ "--x": c.x, "--y": c.y, "--w": c.w, "--h": c.h } as Vars} aria-hidden="true">
                  <i>{n + 1}</i>
                </span>
              ))}
            </div>
            <ol className="notes">
              {p.notes.map((t, n) => (
                <li key={n}>
                  <i aria-hidden="true">{n + 1}</i>
                  {t}
                </li>
              ))}
            </ol>
            <figcaption>{p.caption}</figcaption>
            </figure>
          </div>
        ))}
      </div>
    </div>
  );
}
