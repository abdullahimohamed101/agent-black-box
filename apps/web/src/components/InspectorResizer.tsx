"use client";

import { useRef } from "react";

const MIN = 320;
const STEP = 24;
const maxWidth = () => Math.round(window.innerWidth * 0.6);
const clamp = (n: number) => Math.min(Math.max(n, MIN), Math.max(MIN, maxWidth()));

/**
 * The drag handle on the inspector's left edge. A vertical separator that the pointer drags and the arrow keys nudge;
 * the width lives in the run page's state so the grid column and the handle never disagree.
 */
export function InspectorResizer({
  width,
  onChange,
}: {
  /** Current width in pixels, or null for the stylesheet's default. */
  width: number | null;
  onChange: (px: number) => void;
}) {
  const drag = useRef<{ x: number; w: number } | null>(null);
  const current = (el: HTMLElement) =>
    (width ?? el.nextElementSibling?.getBoundingClientRect().width) || 416;
  return (
    <div
      className="insp-resize"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the inspector"
      aria-valuemin={MIN}
      aria-valuemax={maxWidth()}
      aria-valuenow={width ?? 416}
      tabIndex={0}
      onPointerDown={(e) => {
        drag.current = { x: e.clientX, w: current(e.currentTarget) };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (drag.current) onChange(clamp(drag.current.w + (drag.current.x - e.clientX)));
      }}
      onPointerUp={() => {
        drag.current = null;
      }}
      onKeyDown={(e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        e.stopPropagation();
        onChange(clamp(current(e.currentTarget) + (e.key === "ArrowLeft" ? STEP : -STEP)));
      }}
    />
  );
}
