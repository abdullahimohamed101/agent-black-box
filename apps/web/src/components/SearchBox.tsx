"use client";

import { useId, useState } from "react";
import type { QueryFieldOut } from "@/lib/api/types";
import { runs as t } from "@/copy/runs";
import { MAX_Q_CHARS, type SearchProblem } from "@/lib/search";

/** Where the API said `q` went wrong, drawn under a copy of the text. Text only: the query is never HTML. */
function Caret({ q, position }: { q: string; position: number }) {
  const at = Math.min(Math.max(position, 0), q.length);
  return (
    <pre className="search-caret" aria-hidden="true">
      <code>{q}</code>
      {"\n"}
      <span>{" ".repeat(at)}^</span>
    </pre>
  );
}

/**
 * The text that explains a refused or failed search. It never claims "no results": a refusal is not an empty page.
 * The refused value is not repeated; only the field name the API canonicalised is.
 */
export function SearchProblemNotice({
  problem,
  q,
  onRetry,
}: {
  problem: SearchProblem;
  q: string;
  onRetry?: () => void;
}) {
  if (problem.kind === "invalid")
    return (
      <div role="alert" className="search-problem" data-problem="invalid">
        <p className="state-title">This search could not be understood</p>
        <p>
          {problem.message}
          {problem.position !== null && <> (at character {problem.position + 1})</>}
        </p>
        {problem.position !== null && <Caret q={q} position={problem.position} />}
      </div>
    );
  if (problem.kind === "forbidden")
    return (
      <div role="alert" className="search-problem" data-problem="forbidden">
        <p className="state-title">Your role cannot search that field</p>
        <p>
          {problem.field ? <code>{problem.field}:</code> : "That field"} searches captured content,
          which needs the content permission. Your role does not have it, so no search ran. Metadata
          search (status, agent, tool, model, cost, time) still works.
        </p>
      </div>
    );
  if (problem.kind === "too-long")
    return (
      <div role="alert" className="search-problem" data-problem="too-long">
        <p className="state-title">This search is too long</p>
        <p>Searches are limited to {MAX_Q_CHARS} characters. Shorten it and try again.</p>
      </div>
    );
  return (
    <div role="alert" className="search-problem" data-problem={problem.kind}>
      <p className="state-title">
        {problem.kind === "busy" ? "Search is busy" : "This search took too long"}
      </p>
      <p>
        {problem.message}{" "}
        {problem.kind === "timeout" && "Narrow the time range or the project, then try again."}
      </p>
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

/**
 * The one filter field. Enter commits the text (there is no Search button: the keyboard on a phone says "Search"); the
 * chips under it and the facets beside it edit the same text. The grammar help is a "?" button that opens a panel, not a
 * second control row.
 */
export function SearchBox({
  value,
  fields,
  onSubmit,
}: {
  /** The committed query text (the page's state); the box edits a draft of it. */
  value: string;
  /** The fields this person may search, from the API's echo. Content fields are absent without the permission. */
  fields: readonly QueryFieldOut[] | null;
  onSubmit: (q: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  const [tooLong, setTooLong] = useState(false);
  const [help, setHelp] = useState(false);
  const id = useId();
  // The page may replace the query (a chip removed, a facet added): adopt it while rendering, not in an effect.
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setDraft(value);
  }

  const submit = () => {
    if (draft.length > MAX_Q_CHARS) {
      setTooLong(true);
      return;
    }
    setTooLong(false);
    onSubmit(draft.trim());
  };

  return (
    <div className="search">
      <form
        role="search"
        aria-label={t.search.label}
        className="search-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor={`${id}-q`}>{t.search.label}</label>
        <input
          id={`${id}-q`}
          type="search"
          enterKeyHint="search"
          value={draft}
          autoComplete="off"
          spellCheck={false}
          placeholder={t.search.placeholder}
          aria-invalid={tooLong}
          onChange={(e) => {
            setDraft(e.target.value);
            setTooLong(false);
          }}
        />
        <button
          type="button"
          className="search-help-button"
          aria-label={t.search.help}
          aria-expanded={help}
          aria-controls={`${id}-help`}
          onClick={() => setHelp(!help)}
        >
          <span aria-hidden="true">?</span>
        </button>
      </form>
      {help && (
        <div id={`${id}-help`} className="search-help">
          <p className="muted">{t.search.helpIntro}</p>
          {fields ? (
            <ul className="search-fields" aria-label={t.search.fieldsLabel}>
              {fields.map((f) => (
                <li key={f.name}>
                  <code>{f.name}:</code> <span className="muted">{f.description}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t.search.fieldsLoading}</p>
          )}
        </div>
      )}
      {tooLong && <SearchProblemNotice problem={{ kind: "too-long" }} q={draft} />}
    </div>
  );
}
