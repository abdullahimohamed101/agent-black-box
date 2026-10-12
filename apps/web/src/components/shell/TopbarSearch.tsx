"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import "../../app/topbar-search.css";
import { api, unwrap } from "@/lib/api/client";
import { MAX_Q_CHARS, searchProblem } from "@/lib/search";
import { runPath, runsPath, type Base } from "@/lib/routes";
import { StatusBadge } from "../StatusBadge";
import { useProjectId } from "../WorkspaceProvider";

/** Results shown inline; the rest are one Enter away on the runs list. */
export const TOPBAR_RESULTS = 5;
const DEBOUNCE_MS = 220;

const isEditable = (t: EventTarget | null): boolean => {
  if (!(t instanceof HTMLElement)) return false;
  return (
    t.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) ||
    t.getAttribute("role") === "textbox" ||
    t.getAttribute("role") === "combobox"
  );
};

function useDebounced(value: string, ms: number): string {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Why a search cannot show results, in words that never repeat what was typed. */
function problemText(error: unknown): string {
  const p = searchProblem(error);
  if (!p) return "Search is unavailable right now. Try again in a moment.";
  switch (p.kind) {
    case "forbidden":
      return `Your role cannot search ${p.field ? `${p.field}:` : "that field"}, which holds captured content. Metadata search still works.`;
    case "invalid":
      return `This search could not be understood. ${p.message}`;
    case "too-long":
      return `Searches are limited to ${MAX_Q_CHARS} characters.`;
    case "busy":
      return "Search is busy. Press Enter to open the runs list, or try again.";
    default:
      return "This search took too long. Narrow it and try again.";
  }
}

/**
 * The global search: the runs-list query grammar in the top bar, opened with `/`. It shows the first few matching runs
 * and hands the same text to the runs list on Enter, where the query stays the single source of truth. A viewer's
 * request is refused by the API for content fields and never matches content (ADR-101); this component adds nothing
 * that could tell the two apart.
 */
export function TopbarSearch({ base }: { base: Base }) {
  const router = useRouter();
  const project = useProjectId(base.project);
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const wantFocus = useRef(false);
  const [value, setValue] = useState("");
  const [dismissed, setDismissed] = useState(false);
  const [focused, setFocused] = useState(false);
  const [expanded, setExpanded] = useState(false); // phone: the field replaces the crumbs
  const [active, setActive] = useState(-1);

  const text = value.trim();
  const debounced = useDebounced(text, DEBOUNCE_MS);
  const tooLong = text.length > MAX_Q_CHARS;
  const wanted = debounced !== "" && debounced.length <= MAX_Q_CHARS;
  const results = useQuery({
    queryKey: ["topbar-search", project, debounced],
    enabled: wanted,
    staleTime: 10_000,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/runs", {
          signal,
          params: {
            query: { limit: TOPBAR_RESULTS, q: debounced, project_id: project || undefined },
          },
        }),
      ),
  });

  // `/` focuses the field unless the person is already typing somewhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
      if (isEditable(e.target)) return;
      e.preventDefault();
      wantFocus.current = true;
      setExpanded(true);
      setDismissed(false);
      input.current?.focus(); // wide screens: already visible
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (expanded && wantFocus.current) {
      wantFocus.current = false;
      input.current?.focus();
    }
  }, [expanded]);

  const items =
    wanted && debounced === text ? (results.data?.items ?? []).slice(0, TOPBAR_RESULTS) : [];
  const settled = wanted && debounced === text && !results.isPending;
  const state: "idle" | "loading" | "error" | "empty" | "results" =
    text === ""
      ? "idle"
      : tooLong
        ? "error"
        : !settled
          ? results.isError && debounced === text
            ? "error"
            : "loading"
          : results.isError
            ? "error"
            : items.length === 0
              ? "empty"
              : "results";
  const showPopup = focused && !dismissed && state !== "idle";
  // The last option is "all results in the runs list"; with nothing found it is still offered.
  const optionCount = state === "results" ? items.length + 1 : state === "empty" ? 1 : 0;
  const listId = `${id}-list`;
  const optionId = (i: number) => `${id}-opt-${i}`;

  const toList = () => {
    setDismissed(true);
    router.push(`${runsPath(base)}?${new URLSearchParams({ q: text }).toString()}`);
  };
  const choose = (i: number) => {
    const run = items[i];
    setDismissed(true);
    if (run) router.push(runPath(base, run.id));
    else if (text) toList();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (optionCount === 0) return;
      e.preventDefault();
      setDismissed(false);
      const step = e.key === "ArrowDown" ? 1 : -1;
      // -1 is the input itself, so the cycle has optionCount + 1 stops.
      setActive((a) => ((a + 1 + step + optionCount + 1) % (optionCount + 1)) - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!text) return;
      if (active >= 0 && active < items.length) choose(active);
      else toList();
    } else if (e.key === "Escape") {
      if (showPopup) {
        e.preventDefault();
        setDismissed(true);
        setActive(-1);
      } else {
        setValue("");
        input.current?.blur();
        setExpanded(false);
      }
    }
  };

  return (
    <div
      className="topbar-search"
      data-expanded={expanded ? "true" : undefined}
      onBlur={(e) => {
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        setFocused(false);
        setActive(-1);
        if (text === "") setExpanded(false);
      }}
    >
      <button
        type="button"
        className="topbar-search-icon"
        aria-label="Search runs"
        aria-keyshortcuts="/"
        aria-expanded={expanded}
        onClick={() => {
          wantFocus.current = true;
          setExpanded(true);
        }}
      >
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
          <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path
            d="M10.5 10.5 14 14"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </button>
      <div role="search" aria-label="Find a run" className="topbar-search-field">
        <input
          ref={input}
          type="text"
          role="combobox"
          aria-label="Search all runs"
          aria-autocomplete="list"
          aria-expanded={showPopup && optionCount > 0}
          aria-controls={listId}
          aria-activedescendant={
            showPopup && optionCount > 0 && active >= 0 ? optionId(active) : undefined
          }
          aria-keyshortcuts="/"
          autoComplete="off"
          spellCheck={false}
          placeholder="Search runs"
          value={value}
          onFocus={() => {
            setFocused(true);
            setDismissed(false);
          }}
          onChange={(e) => {
            setValue(e.target.value);
            setDismissed(false);
            setActive(-1);
          }}
          onKeyDown={onKeyDown}
        />
        <kbd className="topbar-search-hint" aria-hidden="true">
          /
        </kbd>
        <div
          id={listId}
          role="listbox"
          aria-label="Matching runs"
          hidden={!showPopup || optionCount === 0}
        >
          {state === "results" &&
            items.map((r, i) => (
              <div
                key={r.id}
                id={optionId(i)}
                role="option"
                aria-selected={active === i}
                className="topbar-option"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(i)}
              >
                <StatusBadge status={r.status} />
                <span className="topbar-option-name">{r.name ?? r.id}</span>
                {r.agent_id && <span className="muted topbar-option-agent">{r.agent_id}</span>}
              </div>
            ))}
          {(state === "results" || state === "empty") && (
            <div
              id={optionId(state === "results" ? items.length : 0)}
              role="option"
              aria-selected={active === (state === "results" ? items.length : 0)}
              className="topbar-option topbar-option-all"
              onMouseDown={(e) => e.preventDefault()}
              onClick={toList}
            >
              Show all in the runs list
            </div>
          )}
        </div>
        {showPopup && state === "loading" && (
          <p className="topbar-note muted" role="status">
            Searching…
          </p>
        )}
        {showPopup && state === "empty" && (
          <p className="topbar-note muted" role="status">
            No runs match this search.
          </p>
        )}
        {showPopup && state === "error" && (
          <p className="topbar-note" role="alert">
            {tooLong
              ? `Searches are limited to ${MAX_Q_CHARS} characters.`
              : problemText(results.error)}
          </p>
        )}
      </div>
    </div>
  );
}
