import { approvalStateMeta, riskMeta, type ApprovalSummary, type StateMeta } from "@/lib/policy";
import { ShieldGlyph } from "./SecuritySeverity";
import "../app/policy.css";

/**
 * Glyph and word marks for approvals and policy outcomes. Colour never carries the meaning and never means a verdict
 * (ADR-111): waiting and expired are amber, a denial is ink weight with an outlined glyph, nothing here is red.
 */
export function OutcomeMark({ meta, ...rest }: { meta: StateMeta } & { "data-testid"?: string }) {
  return (
    <span className="pol-mark" data-tone={meta.tone} {...rest}>
      <span className="pol-glyph" aria-hidden="true">
        {meta.glyph}
      </span>
      <span className="pol-word">{meta.word}</span>
    </span>
  );
}

export function StateMark({ approval }: { approval: Pick<ApprovalSummary, "state" | "consumed"> }) {
  return <OutcomeMark meta={approvalStateMeta(approval)} />;
}

export function RiskMark({ risk }: { risk: string | null | undefined }) {
  const m = riskMeta(risk);
  return (
    <span className="sec-sev" data-tone={m.tone} data-style={m.filled ? "filled" : "outlined"}>
      <ShieldGlyph filled={m.filled} />
      <span className="sec-sev-word">{m.word}</span>
    </span>
  );
}
