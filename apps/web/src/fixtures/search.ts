import type { RunOut } from "@/lib/api/types";
import type { FixtureRun } from "./builder";
import { isContentAttribute } from "./withhold";

/**
 * A stand-in for `GET /v1/runs?q=` in fixture mode, for the field subset the fixtures can answer (status, agent,
 * tool, model, event, started, name and free words over the run name). It is NOT the grammar: the real parser lives in
 * the API. Anything it cannot answer is a `422 QUERY_INVALID` so a fixture session never pretends to search content.
 */

type Term = {
  kind: "field" | "text";
  field: string | null;
  op: string | null;
  value: string | null;
  lexemes: string[] | null;
  phrase: boolean;
  prefix: boolean;
  negated: boolean;
  span: [number, number];
};

const FIELDS = [
  ["status", "enum", "run status"],
  ["agent", "slug", "any agent of the run"],
  ["tool", "name", "tool name"],
  ["model", "name", "model name"],
  ["event", "event", "event type"],
  ["name", "text", "run name contains"],
  ["started", "time", "date or -24h"],
] as const;

/**
 * The fields that search captured content. The fixtures cannot answer them, but a viewer asking for one is refused
 * exactly as the API refuses it (ADR-101): the same `403` whatever the value, never an empty page or a `422`.
 */
const CONTENT_FIELDS = new Set(["file", "command"]);

/** A person without the content permission asked for a content field (ADR-101); `field` is the first one asked for. */
export class FixtureForbiddenError extends Error {
  constructor(readonly field: string) {
    super(`searching ${field} needs the content permission`);
  }
}

export class FixtureQueryError extends Error {
  constructor(
    readonly position: number,
    readonly reason: string,
  ) {
    super(reason);
  }
}

function scan(q: string): Term[] {
  const terms: Term[] = [];
  let i = 0;
  while (i < q.length) {
    if (/\s/.test(q[i]!)) {
      i++;
      continue;
    }
    const start = i;
    let negated = false;
    if (q[i] === "-" && q[i + 1] !== undefined && !/\s/.test(q[i + 1]!)) {
      negated = true;
      i++;
    }
    let text = "";
    let quoted = false;
    while (i < q.length && (quoted || !/\s/.test(q[i]!))) {
      if (q[i] === '"') quoted = !quoted;
      else text += q[i];
      i++;
    }
    const colon = text.indexOf(":");
    if (colon > 0 && /^[A-Za-z_]+$/.test(text.slice(0, colon))) {
      let value = text.slice(colon + 1);
      let op: string | null = "=";
      const m = /^(>=|<=|>|<)/.exec(value);
      if (m) {
        op = m[1]!;
        value = value.slice(op.length);
      }
      const prefix = value.endsWith("*");
      terms.push({
        kind: "field",
        field: text.slice(0, colon).toLowerCase(),
        op,
        value: (prefix ? value.slice(0, -1) : value).toLowerCase(),
        lexemes: null,
        phrase: false,
        prefix,
        negated,
        span: [start, i],
      });
    } else {
      terms.push({
        kind: "text",
        field: null,
        op: null,
        value: null,
        lexemes: text
          .toLowerCase()
          .split(/[^a-z0-9_]+/)
          .filter(Boolean),
        phrase: text.includes(" "),
        prefix: false,
        negated,
        span: [start, i],
      });
    }
  }
  return terms;
}

const UNIT_MS = { m: 6e4, h: 3.6e6, d: 8.64e7, w: 6.048e8 } as const;

function startedMs(value: string): number | null {
  const rel = /^-(\d{1,5})([mhdw])$/.exec(value);
  if (rel) return Date.now() - Number(rel[1]) * UNIT_MS[rel[2] as keyof typeof UNIT_MS];
  const t = Date.parse(value);
  return Number.isNaN(t) ? null : t;
}

const facts = (f: FixtureRun) => {
  // Only metadata attributes are ever indexed: never one that carries captured content (ADR-101 d7).
  const attrs = (k: string) => [
    ...new Set(
      f.events
        .map((e) => (isContentAttribute(k) ? undefined : e.attributes[k]))
        .filter((v): v is string => typeof v === "string"),
    ),
  ];
  return {
    tools: attrs("tool.name"),
    models: attrs("llm.model"),
    event_types: [...new Set(f.events.map((e) => e.event_type))],
    agents: [...new Set(f.events.map((e) => e.agent_id).filter((v): v is string => !!v))],
  };
};

const INDEXED = new Set(["agent", "tool", "model", "event"]);

export function fixtureSearch(
  all: FixtureRun[],
  q: string,
  viewer: { content: boolean } = { content: true },
) {
  const terms = scan(q);
  for (const t of terms) {
    if (t.kind === "field" && CONTENT_FIELDS.has(t.field!)) continue; // judged below, after the parse
    if (t.kind === "field" && !FIELDS.some(([n]) => n === t.field))
      throw new FixtureQueryError(
        t.span[0],
        "fixture mode answers only the fields listed in the search help",
      );
    if (t.kind === "field" && t.field === "started" && startedMs(t.value ?? "") === null)
      throw new FixtureQueryError(t.span[0], "not a date or a relative time");
  }
  // The gate runs after the parse and before anything is read, like the API's: a refusal depends on the field only.
  const content = terms.find((t) => t.kind === "field" && CONTENT_FIELDS.has(t.field!));
  if (content) {
    if (!viewer.content) throw new FixtureForbiddenError(content.field!);
    throw new FixtureQueryError(
      content.span[0],
      "fixture mode answers only the fields listed in the search help",
    );
  }
  const usesIndex = terms.some(
    (t) => (t.kind === "field" && INDEXED.has(t.field!)) || t.kind === "text",
  );
  const matches = (f: FixtureRun, t: Term): boolean => {
    const r = f.run;
    const x = facts(f);
    const v = t.value ?? "";
    const has = (list: string[]) =>
      list.some((s) => (t.prefix ? s.toLowerCase().startsWith(v) : s.toLowerCase() === v));
    switch (t.field) {
      case "status":
        return r.status.toLowerCase() === v;
      case "agent":
        return has(x.agents);
      case "tool":
        return has(x.tools);
      case "model":
        return has(x.models);
      case "event":
        return has(x.event_types);
      case "name":
        return (r.name ?? "").toLowerCase().includes(v);
      case "started": {
        const at = Date.parse(r.started_at);
        const ref = startedMs(v)!;
        return t.op === ">="
          ? at >= ref
          : t.op === "<="
            ? at <= ref
            : t.op === ">"
              ? at > ref
              : at < ref;
      }
      default: {
        const name = (r.name ?? "").toLowerCase();
        return (t.lexemes ?? []).every((w) => name.includes(w));
      }
    }
  };
  const hits = all.filter((f) => terms.every((t) => matches(f, t) !== t.negated));
  const counts = (pick: (f: FixtureRun) => string[]) => {
    const m = new Map<string, number>();
    for (const f of hits) for (const k of pick(f)) m.set(k, (m.get(k) ?? 0) + 1);
    return [...m]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, 10)
      .map(([value, count]) => ({ value, count }));
  };
  const named = (
    f: FixtureRun,
    key: "tools" | "models" | "event_types" | "agents",
    field: string,
  ) => {
    const wanted = terms.filter((t) => !t.negated && t.field === field);
    return facts(f)[key].filter((s) =>
      wanted.some((t) =>
        t.prefix ? s.toLowerCase().startsWith(t.value!) : s.toLowerCase() === t.value,
      ),
    );
  };
  const decorate = (f: FixtureRun): RunOut =>
    usesIndex
      ? ({
          ...f.run,
          matched: {
            tools: named(f, "tools", "tool"),
            models: named(f, "models", "model"),
            event_types: named(f, "event_types", "event"),
            agents: named(f, "agents", "agent"),
            tags: [],
            metadata_truncated: false,
          },
        } as RunOut)
      : f.run;
  return {
    query: {
      terms,
      fields: FIELDS.map(([name, kind, description]) => ({
        name,
        kind,
        description,
        ordered: name === "started",
        prefix: name !== "status" && name !== "name" && name !== "started",
      })),
      content_allowed: false,
      unindexed: usesIndex ? 0 : null,
      unindexed_capped: false,
    },
    runs: hits.map(decorate),
    facets: terms.length
      ? {
          status: counts((f) => [f.run.status]),
          agents: counts((f) => facts(f).agents),
          tools: counts((f) => facts(f).tools),
          models: counts((f) => facts(f).models),
          event_types: counts((f) => facts(f).event_types),
        }
      : null,
  };
}
