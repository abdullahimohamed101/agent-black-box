/** Strings for Experiments and an experiment (docs/design/pass-2/05-microcopy-and-states.md section 11). */

const runs = (n: number) => `${n} finished run${n === 1 ? "" : "s"}`;

export const experiments = {
  title: "Experiments",
  back: { label: "Experiments", name: "All experiments" },
  lede: "Compare variants of an agent: the same task set run with one thing changed.",
  loading: "Loading experiments",
  loadingOne: "Loading experiment",
  listCaption: "Experiments",
  heads: {
    experiment: "Experiment",
    taskSet: "Task set",
    variants: "Variants",
    runs: "Runs",
    created: "Created",
  },
  empty: {
    title: "No experiments yet",
    canWrite: "Create one below.",
    readOnly: "Nobody has created an experiment.",
  },
  create: {
    title: "New experiment",
    help: "An experiment compares variants of one agent setup. Declare each variant's settings (model, prompt version); do not put secrets, prompts or paths in them. This product does not run the tasks: you attach runs you already recorded.",
    name: "Name",
    project: "Project",
    description: "Description (optional)",
    taskSet: "Task set label (optional)",
    evaluators: 'Evaluators used by "Evaluate all"',
    submit: "Create experiment",
    submitting: "Creating…",
    created: "Created.",
    open: (name: string) => `Open "${name}"`,
  },
  detail: {
    taskSet: (label: string) => `Task set: ${label}`,
    archived: "This experiment is archived.",
    warning: "Warning",
  },
  control: {
    none: "Add a candidate variant to compare against the baseline.",
    single: "Controlled:",
    singleBody:
      "each candidate changes exactly one setting from the baseline, so a difference in results can be attributed to it.",
    multiple: "Not controlled:",
    multipleBody:
      "a candidate changes more than one setting (or none), so a difference in results cannot be attributed to a single cause.",
    changes: (variant: string, keys: string) => `${variant} changes: ${keys}`,
    nothing: "nothing",
  },
  sections: {
    compared: "Compared",
    successRate: "Success rate",
    medianCost: "Median cost",
    variants: "Variants",
    metrics: "Metrics",
    runs: "Runs",
  },
  chart: { successRate: "Success rate by variant", medianCost: "Median cost by variant" },
  variants: {
    caption: "Variants",
    heads: {
      variant: "Variant",
      role: "Role",
      settings: "Declared settings",
      differs: "Differs from baseline",
      runs: "Runs",
    },
    nothing: "nothing",
  },
  metrics: {
    caption: "Metrics by variant",
    evaluators: (list: string) => `Evaluators: ${list}.`,
    measure: "Measure",
  },
  attached: {
    caption: "Attached runs",
    none: "No runs are attached yet.",
    heads: {
      run: "Run",
      variant: "Variant",
      status: "Status",
      started: "Started",
      actions: "Actions",
    },
    compare: "Compare with baseline run",
    detach: "Detach",
  },
  attach: {
    title: "Attach a run",
    loading: "Loading runs",
    needVariant: "Add a variant first.",
    none: "No unattached runs in this project.",
    run: "Run",
    variant: "Variant",
    submit: "Attach run",
    submitting: "Attaching…",
  },
  addVariant: {
    title: "Add a variant",
    name: "Name",
    role: "Role",
    config: "Declared settings, one key=value per line",
    help: "Visible to everyone who can see this experiment. No secrets, prompts or paths.",
    submit: "Add variant",
    submitting: "Adding…",
  },
  evaluate: {
    button: "Evaluate all",
    starting: "Starting…",
    help: (cap: number) =>
      `Starts every selected evaluator on every attached run (at most ${cap} evaluations per click; click again while some remain). Evaluations start after a short batch delay so that runs still being summarised go first. A model judge costs money per evaluation.`,
  },
  /** The sentence a detail page opens with (docs section 11.3). Derived from the API's per-variant metrics only. */
  verdict: {
    none: "Not enough finished runs to compare yet: attach finished runs to at least two variants.",
    leadsOnSuccess: (a: string, ra: string, b: string, rb: string) =>
      `${a} leads on success rate: ${ra} against ${rb} for ${b}.`,
    leadsOnCost: (a: string, rate: string, b: string, ca: string, cb: string) =>
      `${a} and ${b} both succeeded in ${rate} of runs, and ${a} cost less, at a median ${ca} against ${cb}.`,
    tie: (a: string, b: string, rate: string) =>
      `No variant leads: ${a} and ${b} both succeeded in ${rate} of runs.`,
    costLess: (ca: string, cb: string) => `It also cost less, at a median ${ca} against ${cb}.`,
    costMore: (ca: string, cb: string) => `It cost more, at a median ${ca} against ${cb}.`,
    costSame: (c: string) => `The median cost is the same, ${c}.`,
    basis: (a: string, na: number, b: string, nb: number) =>
      `Based on ${runs(na)} for ${a} and ${nb} for ${b}.`,
    small: "That is too few to be sure.",
  },
} as const;
