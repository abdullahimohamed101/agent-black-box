/** Runs without a search document cannot match an indexed term; say so instead of letting them silently vanish. */
export function UnindexedNotice({ count, capped }: { count: number | null; capped: boolean }) {
  if (!count) return null;
  return (
    <p className="search-notice" role="status" data-testid="unindexed">
      {capped ? `${count}+` : count} run{count === 1 && !capped ? " is" : "s are"} not yet indexed
      for search, so {count === 1 && !capped ? "it" : "they"} cannot match these terms. New runs are
      indexed automatically; older runs need <code>search-rebuild</code>.
    </p>
  );
}
