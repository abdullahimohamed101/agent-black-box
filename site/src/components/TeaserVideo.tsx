"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

export interface VideoSource { src: string; type: string }

/**
 * The 18 s silent teaser loop (video brief 5.5). The markup has no `autoplay` attribute and shows native controls, so with
 * JavaScript off the poster is a normal video the person can press play on. Once hydrated, the loop starts by itself unless the
 * person asked for reduced motion or Save-Data: then it stays on the poster with one 44 px play control, and nothing moves until
 * it is pressed. Muted, so the browser allows it to start.
 */
// "pending" on the server and during hydration, then whether this person wants no autoplay.
const noop = () => () => {};
function calmSnapshot(): "calm" | "free" {
  const conn = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches || conn?.saveData ? "calm" : "free";
}
const useCalm = () => useSyncExternalStore(noop, calmSnapshot, () => "pending" as const);

export function TeaserVideo({ poster, sources, captions, width, height }: { poster: string; sources: VideoSource[]; captions?: string; width: number; height: number }) {
  const ref = useRef<HTMLVideoElement>(null);
  const calm = useCalm();
  const [pressed, setPressed] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const tap = calm === "calm" ? !pressed : blocked;
  const native = calm === "pending";

  useEffect(() => {
    if (calm === "free") void ref.current?.play().catch(() => setBlocked(true));
  }, [calm]);

  return (
    <div className="video-box">
      <video ref={ref} muted loop playsInline preload="metadata" poster={poster} width={width} height={height} controls={native} aria-label="A failed run, recorded from the demo: silent, with captions in the picture">
        {sources.map((s) => (
          <source key={s.src} src={s.src} type={s.type} />
        ))}
        {captions ? <track kind="captions" src={captions} srcLang="en" label="English" default /> : null}
      </video>
      {tap ? (
        <button
          type="button"
          className="play"
          onClick={() => {
            setPressed(true);
            setBlocked(false);
            void ref.current?.play();
          }}
        >
          Play the loop
        </button>
      ) : null}
    </div>
  );
}
