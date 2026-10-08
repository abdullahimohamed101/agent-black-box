# ADR-032: Syntax Highlighting Is a Local Tokenizer Rendered as React Text

Status: Accepted (implemented in Phase 6)
Date: 2026-10-07

## Context
Diffs of agent-written code must be readable. Mature highlighters (highlight.js, Prism, Shiki) are large and, in their usual use, produce HTML strings that
are injected with `dangerouslySetInnerHTML`. Everything shown here is captured telemetry, hostile input (INV-5, BUILD_PROMPT security): trace content must
never reach the DOM as markup.

## Decision
- No new dependency. `apps/web/src/lib/highlight.ts` is a small regex tokenizer (Python, TypeScript/JavaScript, JSON, shell, YAML, plain) that returns
  `{text, type}` tokens per line. Components render each token as a `<span className="tok-...">` containing a **React text child**; React escapes it. No
  `dangerouslySetInnerHTML`, no `innerHTML`, no markup parsing of trace content anywhere. A test renders `<img onerror>` and `<script>` payloads as a diff and asserts
  that no element other than the highlighter's own spans exists.
- Tokenizing is bounded: lines longer than 2,000 characters are not tokenized, diffs show the first 2,000 lines with "show more".
- The five questions (BUILD_PROMPT, Dependencies): the platform does not provide it; libraries are maintained but add 50-500 kB and an HTML-string API; the surface we
  need (colouring keywords, strings, comments, numbers) is a few dozen lines per language; lock-in is nil; a local implementation is clearer to audit.

## Consequences
Colouring is approximate (KI-043). Replacing it with a library later only needs a token-producing adapter; the safe rendering path stays.
