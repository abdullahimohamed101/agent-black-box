/** Unified-diff parsing for display. Pure text in, plain data out; nothing here produces markup. */
export type DiffLine = {
  kind: "add" | "del" | "ctx" | "meta";
  text: string;
  oldNo: number | null;
  newNo: number | null;
};
export type DiffFile = { header: string[]; lines: DiffLine[] };

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/** Splits a (possibly multi-file) unified diff into files with numbered lines. Tolerates truncated or odd input. */
export function parseDiff(text: string): DiffFile[] {
  const files: DiffFile[] = [];
  let file: DiffFile | null = null;
  let oldNo = 0;
  let newNo = 0;
  let inHunk = false;
  const start = (): DiffFile => {
    file = { header: [], lines: [] };
    files.push(file);
    inHunk = false;
    return file;
  };
  const raw = text.split("\n");
  if (raw[raw.length - 1] === "") raw.pop();
  for (const line of raw) {
    if (line.startsWith("diff --git ") || (!file && line.startsWith("--- "))) {
      start().header.push(line);
      continue;
    }
    const f = file ?? start();
    const h = HUNK.exec(line);
    if (h) {
      oldNo = Number(h[1]);
      newNo = Number(h[2]);
      inHunk = true;
      f.lines.push({ kind: "meta", text: line, oldNo: null, newNo: null });
    } else if (!inHunk) {
      f.header.push(line); // ---, +++, index, mode lines
    } else if (line.startsWith("+")) {
      f.lines.push({ kind: "add", text: line.slice(1), oldNo: null, newNo: newNo++ });
    } else if (line.startsWith("-")) {
      f.lines.push({ kind: "del", text: line.slice(1), oldNo: oldNo++, newNo: null });
    } else if (line.startsWith("\\")) {
      f.lines.push({ kind: "meta", text: line, oldNo: null, newNo: null });
    } else {
      f.lines.push({
        kind: "ctx",
        text: line.startsWith(" ") ? line.slice(1) : line,
        oldNo: oldNo++,
        newNo: newNo++,
      });
    }
  }
  return files;
}

export const diffStats = (files: readonly DiffFile[]): { added: number; removed: number } => {
  let added = 0;
  let removed = 0;
  for (const f of files)
    for (const l of f.lines) {
      if (l.kind === "add") added++;
      else if (l.kind === "del") removed++;
    }
  return { added, removed };
};

/** The new-file path from a diff header (`+++ b/app/x.py`), used to pick a language per file. */
export function diffPath(file: DiffFile): string | null {
  const plus = file.header.find((l) => l.startsWith("+++ "));
  const minus = file.header.find((l) => l.startsWith("--- "));
  const pick = (l: string | undefined) => {
    const p = l
      ?.slice(4)
      .trim()
      .replace(/^[ab]\//, "");
    return p && p !== "/dev/null" ? p : null;
  };
  return pick(plus) ?? pick(minus);
}
