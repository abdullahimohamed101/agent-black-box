/**
 * A small line tokenizer for colouring code (ADR-032). It returns plain `{text, type}` tokens; components render
 * each as a React text child, so trace content can never become markup. Approximate by design (KI-043): no
 * multi-line state, so a string or comment spanning lines is coloured per line.
 */
export type TokenType = "kw" | "str" | "num" | "com" | "fn" | "op" | "plain";
export type Token = { text: string; type: TokenType };
export type Lang = "python" | "typescript" | "json" | "shell" | "yaml" | "plain";

const MAX_LINE = 2000;

const words = (s: string) => new RegExp(`(?:${s.split(" ").join("|")})\\b`, "y");
const PY_KW = words(
  "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield self",
);
const TS_KW = words(
  "abstract as async await break case catch class const continue debugger default delete do else enum export extends false finally for from function if implements import in instanceof interface let new null of private protected public readonly return static super switch this throw true try type typeof undefined var void while yield",
);
const SH_KW = words(
  "if then else elif fi for while do done case esac in function echo cd export git npm pnpm pip python python3 pytest cat grep sudo rm ls",
);

type Rule = [TokenType, RegExp];
const sticky = (src: string, flags = "y") => new RegExp(src, flags);
const STR = sticky(`"(?:[^"\\\\]|\\\\.)*"?|'(?:[^'\\\\]|\\\\.)*'?`);
const NUM = sticky(`0[xX][0-9a-fA-F_]+\\b|\\d[\\d_]*(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b`);

const RULES: Record<Exclude<Lang, "plain">, Rule[]> = {
  python: [
    ["com", sticky("#.*")],
    [
      "str",
      sticky(
        `[rbfRBF]{0,2}(?:"""|''')[^]*?(?:"""|''')|[rbfRBF]{0,2}"(?:[^"\\\\]|\\\\.)*"?|[rbfRBF]{0,2}'(?:[^'\\\\]|\\\\.)*'?`,
      ),
    ],
    ["kw", PY_KW],
    ["fn", sticky("(?<=\\bdef |\\bclass )[A-Za-z_]\\w*")],
    ["num", NUM],
  ],
  typescript: [
    ["com", sticky("//.*|/\\*.*?\\*/")],
    ["str", sticky("`(?:[^`\\\\]|\\\\.)*`?|" + STR.source)],
    ["kw", TS_KW],
    ["fn", sticky("(?<=\\bfunction )[A-Za-z_$][\\w$]*")],
    ["num", NUM],
  ],
  json: [
    ["fn", sticky(`"(?:[^"\\\\]|\\\\.)*"(?=\\s*:)`)],
    ["str", STR],
    ["kw", words("true false null")],
    ["num", sticky("-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b")],
  ],
  shell: [
    ["com", sticky("#.*")],
    ["str", STR],
    ["fn", sticky("\\$\\{?[A-Za-z_]\\w*\\}?")],
    ["kw", SH_KW],
    ["op", sticky("&&|\\|\\||[|;&]")],
    ["num", NUM],
  ],
  yaml: [
    ["com", sticky("#.*")],
    ["fn", sticky("[A-Za-z_][\\w.-]*(?=\\s*:(?:\\s|$))")],
    ["str", STR],
    ["kw", words("true false null yes no")],
    ["num", NUM],
  ],
};

const BY_EXT: Record<string, Lang> = {
  py: "python",
  ts: "typescript",
  tsx: "typescript",
  js: "typescript",
  jsx: "typescript",
  mjs: "typescript",
  json: "json",
  sh: "shell",
  bash: "shell",
  yml: "yaml",
  yaml: "yaml",
};
const BY_NAME: Record<string, Lang> = {
  python: "python",
  typescript: "typescript",
  javascript: "typescript",
  json: "json",
  shell: "shell",
  bash: "shell",
  yaml: "yaml",
};

/** The language to colour with: an explicit name from the event (`file.language`) wins, then the extension. */
export function languageFor(path: string | null | undefined, hint?: string | null): Lang {
  if (hint && BY_NAME[hint.toLowerCase()]) return BY_NAME[hint.toLowerCase()]!;
  const ext = (path ?? "").split(".").pop()?.toLowerCase() ?? "";
  return BY_EXT[ext] ?? "plain";
}

export function tokenize(line: string, lang: Lang): Token[] {
  if (lang === "plain" || line.length === 0 || line.length > MAX_LINE)
    return [{ text: line, type: "plain" }];
  const rules = RULES[lang];
  const out: Token[] = [];
  let plain = "";
  const flush = () => {
    if (plain) out.push({ text: plain, type: "plain" });
    plain = "";
  };
  let i = 0;
  while (i < line.length) {
    let hit: [TokenType, string] | null = null;
    // Keywords and numbers only start at a word boundary, so `classify` is not `class` + `ify`.
    const prev = i > 0 ? line[i - 1]! : " ";
    const boundary = !/[\w$]/.test(prev);
    for (const [type, re] of rules) {
      if ((type === "kw" || type === "num") && !boundary) continue;
      re.lastIndex = i;
      const m = re.exec(line);
      if (m && m.index === i && m[0].length > 0) {
        hit = [type, m[0]];
        break;
      }
    }
    if (hit) {
      flush();
      out.push({ text: hit[1], type: hit[0] });
      i += hit[1].length;
    } else {
      plain += line[i];
      i += 1;
    }
  }
  flush();
  return out;
}
