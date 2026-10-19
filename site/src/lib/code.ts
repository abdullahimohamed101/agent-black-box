// Small, dependency-free syntax colouring for the three kinds of block on the page (shell, Python, JSON). Output is segments,
// rendered as text: nothing here ever becomes markup, and nothing is a colour (the classes map to --color-syntax-* tokens).

/** k keyword, s string, f function, c comment (part of the code), p prompt and n note (shown, never copied). */
export type Cls = "k" | "s" | "f" | "c" | "p" | "n";
export type Seg = string | [string, Cls];
export type Line = Seg[];

const PY_KEYWORDS = new Set(["from", "import", "with", "as", "def", "return", "for", "in", "if", "else", "try", "except", "class"]);

/** Python: strings, keywords, call names, comments. Enough for the SDK snippet; not a lexer. */
export function python(src: string): Line[] {
  return src.split("\n").map((line) => {
    const out: Line = [];
    const re = /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|([A-Za-z_][A-Za-z0-9_]*)(\()?|([^A-Za-z_"'#]+)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(line))) {
      if (m[1]) out.push([m[1], "c"]);
      else if (m[2]) out.push([m[2], "s"]);
      else if (m[3]) {
        if (PY_KEYWORDS.has(m[3])) out.push([m[3], "k"]);
        else if (m[4]) out.push([m[3], "f"]);
        else out.push(m[3]);
        if (m[4]) out.push("(");
      } else if (m[5]) out.push(m[5]);
    }
    return out;
  });
}

/** JSON: strings are the string colour, everything else plain. */
export function json(src: string): Line[] {
  return src.split("\n").map((line) => {
    const out: Line = [];
    const re = /("(?:[^"\\]|\\.)*")|([^"]+)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(line))) out.push(m[1] ? [m[1], "s"] : (m[2] as string));
    return out;
  });
}

export interface ShellCommand {
  cmd: string;
  comment?: string;
}

/** Shell: a `$ ` prompt (not selectable, not copied) and an optional trailing comment (shown, not copied). */
export function shell(cmds: ShellCommand[]): Line[] {
  return cmds.map(({ cmd, comment }) => {
    const line: Line = [["$ ", "p"], cmd];
    if (comment) line.push("  ", [`# ${comment}`, "n"]);
    return line;
  });
}

/** What the Copy button puts on the clipboard: the commands or code only, without prompts and notes. */
export const plain = (lines: Line[]) =>
  lines
    .map((l) =>
      l
        .filter((s) => typeof s === "string" || (s[1] !== "p" && s[1] !== "n"))
        .map((s) => (typeof s === "string" ? s : s[0]))
        .join("")
        .replace(/\s+$/, ""),
    )
    .join("\n");
