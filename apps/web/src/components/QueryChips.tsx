import type { QueryTermOut } from "@/lib/api/types";
import { termText } from "@/lib/search";

/**
 * One chip per term the server parsed. The label is the text the person typed, cut out by the server's character
 * span; the browser never re-parses `q`. Everything is rendered as text.
 */
export function QueryChips({
  q,
  terms,
  onRemove,
}: {
  q: string;
  terms: readonly QueryTermOut[];
  onRemove: (term: QueryTermOut) => void;
}) {
  const shown = terms
    .map((t) => ({ term: t, text: termText(q, t.span) }))
    .filter((c) => c.text !== "");
  if (shown.length === 0) return null;
  return (
    <ul className="query-chips" aria-label="Search terms">
      {shown.map(({ term, text }) => (
        <li key={`${term.span[0]}-${term.span[1]}`} className="query-chip">
          <span>{text}</span>
          <button type="button" aria-label={`Remove ${text}`} onClick={() => onRemove(term)}>
            <span aria-hidden="true">×</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
