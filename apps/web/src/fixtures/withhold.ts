import type { EventOut } from "@/lib/api/types";

// The fixture viewer's read projection, mirroring apps/api/src/abb_api/content.py and runs/content.py (ADR-061):
// for a role without `payload.read` the API replaces command text, paths and the like by a marker, lists the keys in
// `withheld_attributes`, drops payload bodies and says so on the detail, and withholds the names of spans built from
// content. The marker must never reach the screen: the UI shows WITHHELD_TEXT (lib/withheld.ts) instead.
export const API_WITHHELD = "[withheld]";

// Keep in step with CONTENT_ATTRIBUTES in packages/event-schema (registry.py) and the leaf names in api content.py.
const CONTENT_ATTRIBUTES = new Set([
  "shell.command",
  "shell.cwd",
  "file.path",
  "git.repo",
  "git.branch",
  "git.push_target",
  "http.url",
  "test.failing",
]);
const CONTENT_LEAVES = new Set([
  "command", "cmd", "argv", "args", "path", "paths", "filename", "file", "cwd", "url", "uri",
]); // prettier-ignore
const CONTENT_SPAN_KINDS = new Set<string | null>([null, "shell", "file", "git"]);

export const isContentAttribute = (key: string): boolean =>
  CONTENT_ATTRIBUTES.has(key) || CONTENT_LEAVES.has(key.slice(key.lastIndexOf(".") + 1));

/** An event as the API serves it to a role without `payload.read`; `detail` is the single-event endpoint. */
export function withholdEvent(e: EventOut, detail: boolean): EventOut {
  const hidden = Object.keys(e.attributes).filter(isContentAttribute).sort();
  const attributes = Object.fromEntries(
    Object.entries(e.attributes).map(([k, v]) => [k, hidden.includes(k) ? API_WITHHELD : v]),
  );
  return {
    ...e,
    attributes,
    payload: null,
    payload_withheld: detail,
    withheld_attributes: hidden,
  };
}

/** A span with its display name withheld when the name is built from content. */
export function withholdSpan<S extends { name?: string | null; kind?: string | null }>(s: S): S {
  const hide = s.name != null && CONTENT_SPAN_KINDS.has(s.kind ?? null);
  return { ...s, name: hide ? null : s.name, name_withheld: hide };
}
