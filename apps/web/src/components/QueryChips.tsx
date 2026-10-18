import type { QueryTermOut } from "@/lib/api/types";
import { runs as t } from "@/copy/runs";
import { termText } from "@/lib/search";

export type ExtraChip = { key: string; text: string; onRemove: () => void; note?: string };

/**
 * One chip per term the server parsed. The label is the text the person typed, cut out by the server's character
 * span; the browser never re-parses `q`. Everything is rendered as text. `extra` chips stand for filters that do not live
 * in the query text (the "Needs attention" status set from an older link), so every active filter is visible and removable.
 */
export function QueryChips({
  q,
  terms,
  onRemove,
  extra = [],
}: {
  q: string;
  terms: readonly QueryTermOut[];
  onRemove: (term: QueryTermOut) => void;
  extra?: readonly ExtraChip[];
}) {
  const shown = terms
    .map((term) => ({ term, text: termText(q, term.span) }))
    .filter((c) => c.text !== "");
  if (shown.length === 0 && extra.length === 0) return null;
  return (
    <ul className="query-chips" aria-label={t.search.chipsLabel}>
      {extra.map((c) => (
        <li key={c.key} className="query-chip">
          <span>{c.text}</span>
          {c.note && <span className="muted"> ({c.note})</span>}
          <button type="button" aria-label={t.search.remove(c.text)} onClick={c.onRemove}>
            <span aria-hidden="true">×</span>
          </button>
        </li>
      ))}
      {shown.map(({ term, text }) => (
        <li key={`${term.span[0]}-${term.span[1]}`} className="query-chip">
          <span>{text}</span>
          <button type="button" aria-label={t.search.remove(text)} onClick={() => onRemove(term)}>
            <span aria-hidden="true">×</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
