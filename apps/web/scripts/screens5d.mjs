// The screens of the step 5d sweep and the consistency counts (fixtures server, workspace "demo").
const RUN = (n) => `run_${String(n).padStart(26, "0")}`;
const P = "/w/demo/projects/all";
const S = "/w/demo/settings";
export const SCREENS = [
  ["runs", `${P}/runs`],
  ["failed-run", `${P}/runs/${RUN(2)}`],
  ["compare", `${P}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`],
  ["review-approvals", "/w/demo/approvals"],
  ["review-security", "/w/demo/security"],
  ["settings-members", `${S}/members`],
  ["settings-sessions", `${S}/sessions`],
  ["settings-api-keys", `${S}/api-keys`],
  ["settings-policies", `${S}/policies`],
  ["settings-security", `${S}/security`],
  ["settings-quotas", `${S}/quotas`],
  ["settings-retention", `${S}/retention`],
  ["settings-pricing", `${S}/pricing`],
  ["settings-alerts", `${S}/alerts`],
  ["settings-audit", `${S}/audit`],
  ["analytics", `${P}/analytics`],
  ["experiments", `${P}/experiments`],
  ["experiment", `${P}/experiments/exp_${"1".padStart(26, "0")}`],
  ["login", "/login"],
];
