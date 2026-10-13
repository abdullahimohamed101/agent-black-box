"use client";

import { useEffect, useId, useRef, useState } from "react";
import "../app/replay.css";
import { formatDuration } from "@/lib/format";
import { SPEEDS, type ReplayApi } from "@/lib/replay";
import { useMedia } from "@/lib/useMedia";

/**
 * The replay transport bar (Phase 12, D4): a presentation of `useReplay`'s API and nothing else, so it holds no replay
 * state of its own (only whether the phone menu is open). One compact row on a wide window; on a phone it keeps play,
 * step and the scrub bar and moves speed, the toggles and "Leave replay" into a menu. The scrub bar is a real range input
 * with a text value; stepping announces the current event politely through one status region, playing never does.
 */

const NARROW = "(max-width: 40rem)";

const Icon = ({ d }: { d: string }) => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
    <path d={d} fill="currentColor" />
  </svg>
);
const PLAY = "M4 2.5v11l9-5.5z";
const PAUSE = "M3.5 2.5h3v11h-3zM9.5 2.5h3v11h-3z";
const BACK = "M12 2.5v11L4.5 8zM3 2.5h2v11H3z";
const FORWARD = "M4 2.5v11L11.5 8zM11 2.5h2v11h-2z";

export function ReplayTransport({
  replay,
  skewed = false,
}: {
  replay: ReplayApi;
  /** `replayClockSkewed(...)`: timing across agents cannot be trusted. */
  skewed?: boolean;
}) {
  const narrow = useMedia(NARROW);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const menuPanel = useRef<HTMLDivElement>(null);
  const uid = useId();
  const menuId = `${uid}-menu`;
  const faultId = `${uid}-fault`;

  // A tap outside the open menu closes it.
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (t && (menuPanel.current?.contains(t) || menuButton.current?.contains(t))) return;
      setMenuOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [menuOpen]);

  const closeMenu = () => {
    setMenuOpen(false);
    menuButton.current?.focus();
  };
  const onMenuKey = (e: React.KeyboardEvent) => {
    if (e.key !== "Escape" || !menuOpen) return;
    // The menu owns this Esc: it must not also leave replay.
    e.preventDefault();
    e.stopPropagation();
    closeMenu();
  };

  const { total, index, state } = replay;
  const elapsed = state?.elapsedMs ?? 0;
  const faultAt = state?.firstErrorIndex ?? -1;
  const position = `event ${total === 0 ? 0 : index + 1} of ${total}, ${formatDuration(elapsed)}`;
  const faultPct = faultAt >= 0 && total > 1 ? (faultAt / (total - 1)) * 100 : null;

  const speedGroup = (
    <div role="group" aria-label="Playback speed" className="replay-speed">
      {SPEEDS.map((x) => (
        <button
          key={x}
          type="button"
          aria-pressed={replay.speed === x}
          onClick={() => replay.setSpeed(x)}
        >
          {x}x
        </button>
      ))}
    </div>
  );
  const toggles = (
    <>
      <label className="replay-check">
        <input
          type="checkbox"
          checked={replay.pauseAtFirstError}
          onChange={(e) => replay.setPauseAtFirstError(e.target.checked)}
        />
        Pause at first error
      </label>
      <label className="replay-check">
        <input
          type="checkbox"
          checked={replay.groupSteps}
          onChange={(e) => replay.setGroupSteps(e.target.checked)}
        />
        Group steps
      </label>
    </>
  );
  const leaveButton = (
    <button type="button" className="replay-leave" onClick={replay.leave}>
      Leave replay
    </button>
  );

  return (
    <section
      className="replay"
      aria-label="Replay controls"
      data-narrow={narrow || undefined}
      data-playing={replay.playing || undefined}
    >
      <div className="replay-row">
        <div className="replay-buttons">
          <button
            type="button"
            className="replay-play"
            onClick={replay.togglePlay}
            aria-label={replay.playing ? "Pause" : "Play"}
            disabled={total === 0}
          >
            <Icon d={replay.playing ? PAUSE : PLAY} />
            <span>{replay.playing ? "Pause" : "Play"}</span>
          </button>
          <button
            type="button"
            className="replay-icon"
            onClick={replay.stepBack}
            disabled={replay.atStart}
            aria-label="Step back"
          >
            <Icon d={BACK} />
          </button>
          <button
            type="button"
            className="replay-icon"
            onClick={replay.stepForward}
            disabled={replay.atEnd}
            aria-label="Step forward"
          >
            <Icon d={FORWARD} />
          </button>
        </div>
        {!narrow && speedGroup}
        <div className="replay-track">
          <input
            type="range"
            className="replay-scrub"
            aria-label="Replay position"
            aria-valuetext={position}
            aria-describedby={faultPct != null ? faultId : undefined}
            min={0}
            max={Math.max(0, total - 1)}
            step={1}
            value={index}
            disabled={total < 2}
            onChange={(e) => replay.seek(Number(e.target.value))}
          />
          {faultPct != null && (
            <span className="replay-fault" style={{ left: `${faultPct}%` }} aria-hidden="true" />
          )}
          {faultPct != null && (
            <span id={faultId} className="sr-only">
              First error at event {faultAt + 1}
            </span>
          )}
        </div>
        <span className="replay-time" data-testid="replay-time">
          {formatDuration(elapsed)}
          <span className="replay-time-total"> / {formatDuration(replay.durationMs)}</span>
        </span>
        {narrow ? (
          <div className="replay-menu">
            <button
              ref={menuButton}
              type="button"
              className="replay-icon"
              aria-expanded={menuOpen}
              aria-controls={menuId}
              onClick={() => setMenuOpen((o) => !o)}
              onKeyDown={onMenuKey}
            >
              Options
            </button>
            {menuOpen && (
              <div
                ref={menuPanel}
                id={menuId}
                role="group"
                aria-label="Replay options"
                className="replay-panel"
                onKeyDown={onMenuKey}
              >
                {speedGroup}
                {toggles}
                {leaveButton}
              </div>
            )}
          </div>
        ) : (
          <>
            {toggles}
            {leaveButton}
          </>
        )}
      </div>
      <div className="replay-notes">
        {/* Never a live region: while playing this changes with every skipped gap. */}
        <p className="replay-gap" aria-live="off" data-testid="replay-gap">
          {replay.pausedAtError ? "Paused at the first error." : (replay.gapNote ?? "")}
        </p>
        {skewed && <p className="replay-skew">Timing across agents is approximate.</p>}
        {replay.notice === "unknown-playhead" && (
          <p className="replay-notice" role="status">
            That moment is not in this run, so replay starts at the beginning.{" "}
            <button type="button" className="replay-link" onClick={replay.dismissNotice}>
              Dismiss
            </button>
          </p>
        )}
      </div>
      {/* Stepping, seeking and the first-error pause announce the event; a tick never changes this text. */}
      <p role="status" className="sr-only">
        {replay.announcement}
      </p>
    </section>
  );
}
