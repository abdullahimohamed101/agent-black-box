/** The run page's tabs. The timeline is the absence of `?view=`; the others are its value. */
export type RunView = "timeline" | "waterfall" | "evidence" | "findings" | "evaluations";

export const RUN_VIEWS: readonly RunView[] = [
  "timeline",
  "waterfall",
  "evidence",
  "findings",
  "evaluations",
];

export const viewFromParam = (v: string | null | undefined): RunView =>
  (RUN_VIEWS as readonly string[]).includes(v ?? "") ? (v as RunView) : "timeline";
