import { comparePath, type Base } from "./routes";

/** How many runs the runs list lets a person pick to compare. */
export const COMPARE_PICKS = 2;

/** Toggle a run in the selection (oldest pick first). A third pick is refused: the caller disables the other boxes. */
export function toggleRun(selected: readonly string[], runId: string): string[] {
  if (selected.includes(runId)) return selected.filter((id) => id !== runId);
  return selected.length >= COMPARE_PICKS ? [...selected] : [...selected, runId];
}

/** The compare link for a full selection: the first pick is the left side (the path's run), the second the right. */
export function compareHref(base: Base, selected: readonly string[]): string | null {
  const [left, right] = selected;
  return selected.length === COMPARE_PICKS && left && right ? comparePath(base, left, right) : null;
}
