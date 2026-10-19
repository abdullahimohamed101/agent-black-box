# Agent BlackBox: positioning brief for the public launch

Status: written 2026-10-18 for Phase 20 (D1, D2, G1). This is the shared source of truth for *what we say* on the landing page (`site/`), the README, the Show HN post and the silent demo video. The landing-page design brief and the video direction inherit from it. Every claim below is tied to a row of the "Claims to verify" table in `docs/plans/active/phase-20-release-polish.md`; nothing may be said in public that is not VERIFIED or qualified exactly as that table says. Name casing is "Agent BlackBox" in prose (D15); slugs stay `agent-black-box` and `blackbox`.

Evidence: product behaviour was read from `docs/design/pass-2/*.md`, `docs/screenshots/pass-2/*.png`, `ARCHITECTURE.md`, `docs/architecture/integrations.md` and the detector sources in `apps/api/src/abb_api/findings/detectors/`. External patterns come from the sources listed at the end; no competitor text is copied.

---

## 1. Positioning and headline

**One-sentence positioning (internal, the sentence every other sentence must agree with):**

> Agent BlackBox is a self-hosted flight recorder for AI agents: it records every model call, tool call, command, file edit and retry as typed events, then shows you the one line where the run broke.

Why this and not "observability platform": the research says agent builders' real complaints are *silent* failures, loops that burn money, and not being able to see tool calls and intermediate steps (CNCF; the arXiv fault taxonomy; HN commenters on budget checks that "fail silently" and the "let me try something else" loop). "Flight recorder" promises a record you can trust after the fact; "platform" promises nothing. The product's one designed signature, the red fault line, is the promise drawn (`docs/design/pass-2/01-direction.md`, "One Red Line").

**Headline options, ranked:**

1. **"See what your agent actually did."** (5 words.) Recommended. It is the README's own sentence, the trust promise in plain words, and it names the job (not the category). It survives being read by a skeptic because it claims a record, not an outcome. Sub: "A self-hosted flight recorder for AI agents. Every model call, tool call, command and retry, and the step where it broke."
2. **"Find the step where your agent broke."** Strong, concrete, maps to the headline sentence on the run page ("Failed at step 7 after 6.2 s: run_migration, ConnectionTimeout."). Weaker as a hero because it only covers failed runs; cost, security and approvals need a second sentence.
3. **"A flight recorder for AI agents."** Clear category, memorable, but a noun phrase does no work for the reader and "flight recorder" is the metaphor, not the benefit. Use it as the one-line repo description and the social-card line, not the hero.

Rejected: "Observability for AI agents" (a category every competitor already owns: LangSmith's hero is "know what your agents are really doing"; Phoenix's is "open-source AI observability & evaluation"); anything with "trust", "reliable" or "production" as an adjective (unverifiable and the claims table bans "production ready").

## 2. Target reader and jobs to be done

**Primary: the engineer who builds and runs an agent and gets paged by it.** Python, one to a few agents (a coding agent, a data or ops agent, a LangGraph graph, an MCP-backed assistant), running on their own infrastructure or a laptop. They already have logs and have learned that logs show what happened but not why the agent deviated (HN, "intent-execution gap"). They do not want their prompts and tool outputs on someone else's server (Langfuse Launch HN: "I don't feel comfortable ... proxying my llm calls through a 3rd party"; sensitive customer data in traces is "impractical" to keep externally).

Their jobs, in their words:
- "It failed overnight. Where, and what was it doing right before?"
- "Why did this run cost $21 when it normally costs 40 cents?"
- "It retried the same thing six times. Show me."
- "Did it run anything destructive, touch a secrets file, push with --force?"
- "I changed the prompt. Did the run actually go differently, and where?"
- "I want it to stop and ask me before it pushes to main."

**Secondary: the tech lead or platform engineer who has to let agents near real systems.** Their jobs: "Can I prove what the agent did if something goes wrong?", "Can I keep this on our boxes?", "Will adding this crash the agent or leak secrets?", "Who approved that action?". The Replit database-deletion incident of July 2025 (agent ran destructive commands past a code freeze, then misreported what it had done) is the story in their head; we never cite it as fear marketing, but the Review page and the approvals state machine answer it.

Not a target for launch: enterprise buyers (no SSO guide, no hosting guide, KI-188/KI-067), non-Python stacks, people who want a hosted service.

## 3. Proof points that are true today

Each proof point names the screen that proves it and the claims-table row that gates it. The landing page shows the screen, not an illustration (D11: screenshots come from the real stack with demo data).

| # | Proof point (the sentence we may say) | Screen | Claims-table row / evidence |
| --- | --- | --- | --- |
| P1 | **One sentence says where it broke; one red line marks the step.** "Failed at step 7 after 6.2 s: run_migration, ConnectionTimeout." with the fault line on the timeline and the inspector open on the failed tool call. | Run page, Timeline tab, inspector open (`step5b-failed-inspector`) | Not a table row; it is product behaviour, verified by the fixtures e2e and by AC10's screenshots from the demo stack. |
| P2 | **Typed events, not log strings.** Model calls carry tokens and cost; tool calls carry name, duration and error type; retries are retries; file edits are diffs; commands are commands. | Timeline rows (`llm.request.completed model-x · 2,300 in / 340 out · $0.0189`, `tool.call.failed run_migration · ConnectionTimeout`, `retry.attempted`) and the Evidence tab | INV-6, `packages/event-schema` (43 event types). Also the "what it records" section of the site (plan step 13). |
| P3 | **Findings point at evidence.** Loops, retry storms, identical requests, context growth, cost spikes, failure chains; six security detectors (destructive command, credential in output, secret file read, risky git, privileged database statement, unexpected destination). Each finding is a fixed sentence with "Show evidence" linking the exact events. | Findings tab (`step5b-failed-findings`), Review > Security findings (`step5c-review-security`) | Detector list from `findings/detectors/`; wording "Probable:" and "Rule:" is the API's fixed text. Say "deterministic detectors", never "AI-powered". |
| P4 | **Cost and wasted spend, per run and per agent.** "$22.16 spent in the last 7 days, $1.23 per run"; "Where the money was wasted: $0.45, failed runs $0.19, retries $0.26, after the first error $0.11." | Analytics > Cost (`step5d-analytics-cost`); the run header's "$0.0167 spent after" the first error | Phase 7 and 22 (ADR-112). Qualifier the UI already carries: a figure is reported, computed from tokens and a versioned price, or estimated; unpriced calls count as $0. |
| P5 | **Multi-agent runs in one waterfall; compare two runs step by step.** Lanes per agent, critical path, "No end recorded"; Compare says "The runs diverged at step 22: A read app/session.py, B read app/oauth.py." | Waterfall tab (`step5b-failed-waterfall`, the multi-agent demo run), Compare (`step5b-compare`) | Phase 9 (ADR-070/071), Phase 11 (ADR-092). The multi-agent example is scripted-only (D10, Q2) and says so. |
| P6 | **Approvals and policies: an agent that asks through the SDK stops until a person decides.** "An approval authorises one action, once." Risk class, policy version, expiry and the exact arguments are on screen. | Review > Approvals, request panel (`step5c-review-approvals-detail`) | Phase 14. Mandatory qualifier, verbatim from the product: "Policies apply to agents that ask through the SDK guard." Never "prevents", "blocks all", "guardrails". |

Supporting facts that may be stated with their qualifiers: self-hosted on Docker Compose with PostgreSQL only (row "Self-hosted": UNVERIFIED until AC12 runs); the demo needs no API key and no network and produces the same runs every time (rows "No API key needed", "Deterministic demo": UNVERIFIED until step 10); secrets are redacted before anything is stored and capture is metadata-only by default (row "Redacts secrets": VERIFIED in Phase 6, re-run in step 10; `docs/SECURITY.md`); the SDK is standard-library Python and never raises into the host agent (INV-4, `packages/sdk-python`); adapters for LangGraph, OpenAI, Anthropic and MCP, "tested offline against the real libraries, not against live providers" (row "Works with"); no third-party request from the product or the site (rows AC11/AC15: UNVERIFIED until step 17).

## 4. What we do not say

- **Hype words, banned on every surface:** seamless, powerful, effortless, intuitive, enterprise-grade, production-ready, battle-tested, blazing, revolutionary, game-changing, next-generation, "AI-powered", "intelligent", "smart", "magic", "supercharge", "10x", "complete visibility", "full observability", "end-to-end", "guardrails", "the only", "the best". Developers read these as the absence of a measurable claim (Instruqt, Okoone, Tech Audience Accelerator). Also banned: exclamation marks, "please", "oops", emoji (the product's own voice rules in `05-microcopy-and-states.md`).
- **Unverifiable claims:** any number we have not measured (overhead in ms, events per second, "handles millions"); "scales" (ARCHITECTURE.md: no load test exists); "secure" as an adjective (we say what is redacted and what is not); "real-time" (we say "live", which the SSE path delivers, and "about a second" for derived state); "catches every loop" (detectors are deterministic rules with named limits); "stops dangerous actions" (enforcement is cooperative).
- **The real-model path.** `--live` exists on two examples and "has not been run by the project" (KI-044). It is never shown, captioned, screenshotted or implied. If mentioned at all: "A `--live` flag exists; the project has not run it."
- **Comparisons we cannot back.** No "alternative to LangSmith/Langfuse/Phoenix" in the title or hero: we have no feature matrix, no benchmark, and HN readers of the Langfuse launch already could not tell the incumbents apart ("having a hard time figuring out differences"). We may describe the honest difference in the FAQ (section 7) and nowhere else.
- **Social proof we do not have.** No logos, no star counts, no "trusted by", no testimonials, no "join N developers". An empty proof strip is worse than none; the proof is the screenshots and the five-command quickstart.
- **Hosting promises.** No SaaS, no "cloud coming soon", no SOC 2, no SSO, no uptime. The product's `/` is a login page, not a marketing page (D2).
- **Status inflation.** "Early release", no version number until a tag exists (Q6); no test counts or phase tables (D14).

## 5. Tone and voice

Rules (they extend the product's own: sentences, not labels; one verb per button; numbers carry units; never echo captured content):

1. Say what it records, not what it gives you. A record is checkable; "insight" is not.
2. Specific noun, specific number, or nothing. "Six security detectors" beats "security monitoring".
3. Admit the limit in the same sentence as the capability. Skeptics stop reading at the first unqualified superlative (Instruqt; StateShift).
4. Second person, present tense, active voice. Short sentences. No semicolon chains in headlines.
5. Name the mechanism when it is the reason to believe: "typed events", "redacted before it is stored", "PostgreSQL only".
6. The product's grammar is the site's grammar: "Failed at step 7", "First error", "Show evidence", "An approval authorises one action, once."
7. Never dramatise failure. Red is for the fault line; copy does not need adjectives to make a failure feel bad.
8. "You" is the engineer. "It" is the agent. "We" is the project, used only for commitments ("We do not send any request to a third party").

Ten before/after pairs:

| | Before | After |
| --- | --- | --- |
| 1 | Complete end-to-end visibility into your AI agents. | Every model call, tool call, command, file edit and retry, as typed events. |
| 2 | AI-powered root-cause analysis finds problems instantly. | Deterministic detectors flag loops, retry storms, cost spikes and six kinds of risky action, and link to the events they were built from. |
| 3 | Blazing-fast, zero-overhead SDK. | A standard-library Python SDK with a bounded buffer. If it fails, your agent keeps running. |
| 4 | Enterprise-grade security built in. | Secrets are redacted before anything is stored. Capture is metadata-only by default. |
| 5 | Prevent your agents from taking dangerous actions. | An agent that asks through the SDK guard stops until a person approves. Code that bypasses the guard is not stopped. |
| 6 | Cut your LLM costs by up to 40%. | See what each run cost, and how much was spent after the first error. |
| 7 | Get started in seconds with our seamless integration. | Five commands and a Docker daemon. The demo runs without an API key or network. |
| 8 | Works with every framework. | Adapters for LangGraph, OpenAI, Anthropic and MCP, tested offline against the real libraries. |
| 9 | Trusted by developers worldwide. | (nothing; show the screenshot) |
| 10 | Never miss a failure again. | When a run fails, a security finding appears or spend passes a threshold, a webhook, Slack or email is sent. Alerts carry names and counts, never content. |

## 6. Naming and glossary

Use the product's words everywhere; a reader who learns a term on the site must find it on the screen.

| Term | Meaning as the product uses it | Do not say |
| --- | --- | --- |
| **Agent BlackBox** | the product; one word "BlackBox" in prose | Agent Black Box, ABB, "the platform" |
| **run** | one user-meaningful execution of an agent, the thing in the Runs list; has a status (Success, Failed, Timed out, Blocked, Running, Awaiting approval, Cancelled) | trace, session, job, request |
| **event** | an immutable, typed, point-in-time record (`llm.request.completed`, `tool.call.failed`, `retry.attempted`, `file.modified`, `shell.command.completed`) | log, log line, message |
| **span** | a duration-bearing operation; the bars in the waterfall | trace segment |
| **step** | an event's position in the run, as the headline and Compare count them ("Failed at step 7") | turn, iteration |
| **first error** and **fault line** | the first error event of a run; the fault line is the red rule drawn at it, labelled "First error, step N". "Fault line" is the design term; the UI label is "First error" | root cause, crash point |
| **headline** | the one-sentence summary at the top of a run ("Failed at step 7 after 6.2 s: run_migration, ConnectionTimeout.") | summary, AI summary |
| **finding** | a derived observation from a deterministic detector, with a severity (Critical, Warning, Info; security: Critical, High, Medium, Low, Info), fixed wording ("Probable: ...", "Rule: ...") and evidence | alert, insight, anomaly (except in the detector's own name), incident |
| **evidence** | the events a finding was built from ("Show evidence"); also the Evidence tab, the artifacts of a run (diffs, shell output) | proof, logs |
| **detector** | a named rule that produces findings; switchable per project under Capture and detectors | AI, model, classifier |
| **waterfall** | the span view with one lane per agent, critical path and "No end recorded" | Gantt, flame graph |
| **Compare** | two runs aligned step by step; "The runs diverged at step 22"; "First outcome difference" | diff, regression test |
| **Review** | the queue of things that wait for a person: Approvals and Security findings | inbox, alerts |
| **approval** | a human decision that authorises one specific action, once; states Waiting, Approved, Denied, Expired, Cancelled; risk class R1-R4 | permission, guardrail |
| **policy** | per-project YAML rules; first match decides; every save is a version; "applies to agents that ask through the SDK guard" | guardrails, rules engine |
| **guard** | `guard()` / `@guarded` in the SDK; the point where an agent asks before acting | interceptor, firewall |
| **capture mode** | per project: metadata-only (default), full (payloads kept after redaction), disabled | privacy mode |
| **redaction** | scanning every string for secret-shaped values before it is stored; a `[REDACTED:credential]` marker remains | masking, scrubbing (reserved for logs) |
| **wasted spend** | cost of runs that failed, timed out or were blocked, plus retries in runs that did not, plus spend after the first error | burn, overspend |
| **scope** | the workspace plus project pair in the top bar | tenant, org |
| **live** | the run page updating over SSE while the run is in progress | real-time |
| **self-hosted** | you run the compose stack (API, worker, web, PostgreSQL) yourself | on-prem, private cloud |

## 7. Messaging hierarchy for the landing page

The site is static HTML and CSS, no script (D12), so every block is text plus a screenshot plus a link. Order and wording:

**Hero.**
- H1: See what your agent actually did.
- Sub (one sentence, two lines at most): A self-hosted flight recorder for AI agents. Every model call, tool call, command and retry, as typed events, and the step where it broke.
- Primary CTA: **Run the demo** (anchor to the quickstart block). Secondary: **Source on GitHub** (G2-gated; until the repo is public the link does not exist and the hero has one CTA). No "Get started free", no "Sign up": there is nothing to sign up for.
- Below the fold line: "Apache-2.0 · Python 3.12 · Docker · PostgreSQL only" (each item a verified row; "Apache-2.0" appears only after AC1 and G2).
- Hero image: the failed run with the fault line and the inspector open (P1). It is the one screen that proves the headline.

**Three proof blocks (screenshot left, 40-60 words right, one h2 each).**
1. *Where it broke.* The headline sentence, the fault line, Go to first error, the inspector's "What went wrong". (P1, P2)
2. *What went wrong, with evidence.* Findings and security findings; "Show evidence" opens the event. List the detectors by name. (P3)
3. *What it cost, and what was wasted.* The cost headline and "Where the money was wasted". (P4)

**Second row, two smaller blocks.** *Many agents, one picture* (waterfall, P5) and *A person in the loop* (approval panel, P6, with the cooperative-enforcement qualifier in the caption).

**Quickstart block ("Run it in about two minutes").** The five commands from `docs/quickstart.md` verbatim (executed by `scripts/docs-commands-check.sh`, AC12), then: "`make demo` records three scripted agents: a coding agent that fails a test and retries, a small custom agent, and a five-agent run. No API key, no network. The same runs, findings and costs every time." Then the four-line SDK snippet from the README.

**What it records (a plain list, no icons).** The event families with one example each; "Captured telemetry is treated as hostile input"; "Accepted events are immutable; summaries, costs and findings are rebuilt from them."

**What stays on your machine.** Three sentences: redaction before storage and metadata-only by default; no request to any third party from the product or this page (AC15/AC11); you run PostgreSQL and nothing else. State the limit: "Stored events are never scrubbed retroactively; unknown-shaped secrets can pass in full capture mode" (`docs/SECURITY.md`).

**Architecture in one picture.** The README's six-line diagram as inline SVG, with the sentence "Simple on purpose: no Kafka, no ClickHouse, no Redis until a measured trigger says otherwise."

**Objections, answered plainly (an FAQ of four).**
- *Why not just logs?* Logs are strings you grep after the fact; they do not know a retry from a tool call, cannot price a model call, and nobody writes the first-error line. Agent BlackBox records typed events, derives the headline, cost and findings from them, and links each finding to the events it was built from. If your logs already answer "which step broke and what did it cost after that", you do not need this.
- *Why not LangSmith or Langfuse?* They are more mature and have evaluation and prompt tooling we do not. Agent BlackBox is narrower: a record you run yourself, with PostgreSQL only, deterministic detectors for loops, cost and risky actions, approvals for actions that should wait for a person, and a demo that needs no key. If you want a hosted service or LangChain-native evaluation workflows, use them.
- *Why not OpenTelemetry?* Use it too. Agent BlackBox maps to OTel at the boundary and can export its own traces over OTel (ADR-170); it does not ingest OTLP today. The canonical event contract exists because agent concepts (retry, approval, file edit, policy decision) need to be typed, and the generative-AI semantic conventions are still moving.
- *Does it stop a bad action?* Only if the agent asks: `guard()` and the MCP adapter check the policy before acting and wait for a person when the policy says so. Code that does not go through the guard is recorded, not stopped.

**Honest status line (footer).** "Early release. Single-node Docker Compose. No hosting guide yet (TLS, domain, real OIDC are documented as not covered). Real-model mode exists in two examples and has not been run by the project."

**CTA wording, everywhere:** verbs that name the next action and nothing more: "Run the demo", "Read the quickstart", "Source on GitHub", "Read the security notes". Never "Get started", "Try it free", "Book a demo".

**Show HN post (G2).** Title: `Show HN: Agent BlackBox – self-hosted flight recorder for AI agents (Python, Postgres)`. Link the repository, not the site (HN guidelines favour something people can run; founders report the repo converts better). First comment, in order: what it records and why typed events; the demo (no key); one honest limitation per paragraph (cooperative enforcement, no load test, `--live` untested, no hosting guide); the honest comparison above; what we want feedback on. Reply within the first hour. No asks for upvotes.

### Caption bank for the silent demo video (G1)

Each caption is at most nine words, plain, and bound to a visible moment. The video has no music (D17). Captions 24-26 are gated on G2. Scenes follow the Phase 20 screenshot set (D11), not the pre-redesign storyboard.

| # | UI moment | Caption |
| --- | --- | --- |
| 1 | Runs list, cursor still | Agent BlackBox records what your agents did. |
| 2 | The 7-day sentence above the list | Last 7 days: 18 runs, 4 failed. |
| 3 | Cursor over the Failed row "Migrate billing schema" | A failed run. Open it. |
| 4 | Run page header, headline sentence | One sentence says where it broke. |
| 5 | Figures line (duration, cost, tokens, retries, errors) | Duration, cost, tokens, retries, errors. |
| 6 | "2 retries · $0.0167 spent after" | Spend after the first error, counted. |
| 7 | Timeline, the red fault line appears | The red line is the first error. |
| 8 | Press `e`; the inspector opens on tool.call.failed | Press e. The failed tool call opens. |
| 9 | Inspector "What went wrong: run_migration failed: ConnectionTimeout" | What went wrong, from the event itself. |
| 10 | Scroll the rows: llm.request.completed with tokens and cost | Every model call carries tokens and cost. |
| 11 | retry.attempted rows | Retries are retries, not log lines. |
| 12 | Findings tab, Critical and Warning findings | Findings, from deterministic detectors. |
| 13 | Click "Show evidence"; the timeline jumps | Each finding links to its evidence. |
| 14 | Review > Security findings, the list | Destructive commands, secret reads, risky git. |
| 15 | A security finding's "Rule:" line | Every finding states its rule. |
| 16 | Runs list, open "Ship feature: multi-agent", Waterfall | Five agents, one run, one picture. |
| 17 | Hover the critical path edge | The thick edge is the critical path. |
| 18 | Compare: "The runs diverged at step 22" | Two runs, aligned step by step. |
| 19 | "First outcome difference" row | Where the outcome first differed. |
| 20 | Analytics > Cost headline | What the week cost, per run. |
| 21 | "Where the money was wasted" | Failed runs, retries, spend after errors. |
| 22 | Review > Approvals, a Waiting request | The agent asked. A person decides. |
| 23 | Panel: "Approving covers exactly this command, once." | One approval, one action, once. |
| 24 | Settings > Capture and detectors, mode metadata-only | Metadata-only by default. Secrets redacted first. |
| 25 | Terminal: `make demo` running, then run URLs | No API key. Same runs every time. |
| 26 | Wordmark on the canvas (G2 gate) | Open source. Self-hosted. Apache-2.0. |
| 27 | Light scheme on the run page | Light and dark follow your system. |
| 28 | Final frame, repository URL (G2 gate) | github.com/abdullahimohamed101/agent-black-box |

Rules: never show `--live`; never show a real email (fixture `owner@local.test` only); caption 25's determinism line waits for AC8; captions 26 and 28 wait for G2; the ADR-170 OTel exporter is not shown (it is off by default).

## 8. Claims to hold until G2 (repo public) or a named gate

| Claim | Hold until | Why |
| --- | --- | --- |
| "Open source", "Apache-2.0", any licence badge | G2 (repo public) and AC1 | A licence file in a private repo is not open source yet. |
| "Source on GitHub" link, the Show HN post, caption 26/28 | G2 | The URL must resolve. |
| "Self-hosted in minutes" / the five commands | AC12 (`docs-commands-check.sh` green on a clean machine) | Row "Self-hosted" is UNVERIFIED (Docker needed). |
| "No API key, no network" and "same runs every time" | AC7, AC8 | Rows "No API key" and "Deterministic demo" are UNVERIFIED until step 10. |
| "No third-party requests" (product and site) | AC11, AC15 | Playwright request log, step 17. |
| "Secrets redacted before storage" | Re-run of the planted-secret e2e in step 10 | VERIFIED in Phase 6; must be re-run against the final demo. |
| "Works with LangGraph, OpenAI, Anthropic, MCP" | Always qualified: "tested offline, not against live providers" | Row "Works with": Qualified. |
| Anything about `--live` or real models | Until the owner runs it and approves (KI-044) | Never run by the project. |
| `pip install agent-black-box` | Q4 (PyPI name check and publication) | The in-app setup guide shows the from-checkout line (review P2-4); the README must say "install from source" until a package exists. The site must not show the pip line before publication. |
| Performance, overhead, scale numbers | A recorded load test (spec §158) | None exists. |
| "Alerts" beyond "webhook, Slack or email" | None, but keep it to the one sentence in before/after 10 | KI-168 filters are deferred; do not show alert history. |
| Any comparison table | Indefinitely | No benchmark; FAQ prose only. |

## Open questions for the owner

1. Is "See what your agent actually did." acceptable as the hero, given it is also the README's first sentence? (Recommended: yes; the repetition is the point.)
2. May the FAQ name LangSmith and Langfuse, or should it say "hosted tracing products"? (Recommended: name them; HN readers will anyway.)
3. Should the landing page show the `pip install` line at all before PyPI publication (Q4), or only the from-source install? (Recommended: from-source only until a package exists.)
4. The footer status line admits four limits in one breath. Keep all four, or move `--live` and the hosting gap to the README only? (Recommended: keep all four on the site.)
5. Show HN title: lead with "self-hosted flight recorder" (recommended) or "open-source observability"? The first is ours; the second is the crowded shelf.

---

## Sources

Product (read in this worktree): `README.md`; `ARCHITECTURE.md`; `docs/plans/active/phase-20-release-polish.md` (D1-D17, claims table, KI-044); `docs/design/pass-2/01-direction.md`, `05-microcopy-and-states.md`; `docs/screenshots/pass-2/` (runs, failed run, inspector, waterfall, findings, compare, analytics cost, review security, approvals detail); `docs/architecture/integrations.md`; `docs/SECURITY.md`; `apps/api/src/abb_api/findings/detectors/`; `docs/design/video-storyboard.md`; spec §159 glossary.

External:
- Hacker News Show HN guidelines: https://news.ycombinator.com/showhn.html
- Launch HN: Langfuse thread (self-hosting praise, proxying distrust, OTel requests, "hard time figuring out differences"): https://news.ycombinator.com/item?id=42441258
- Ask HN: How are you monitoring AI agents in production? (OTel/LGTM, "intent-execution gap", giving agents production access): https://news.ycombinator.com/item?id=47301395
- HN comments on agent loops and silent budget failures: https://news.ycombinator.com/item?id=48974910 and https://news.ycombinator.com/item?id=48794109
- CNCF, "You can't debug what you can't see: observability for AI agents": https://www.cncf.io/blog/2026/08/04/you-cant-debug-what-you-cant-see-observability-for-ai-agents/
- Characterizing Faults in Agentic AI (taxonomy; runaway API usage, polling loops): https://arxiv.org/pdf/2603.06847
- Detecting Silent Failures in Multi-Agentic AI Trajectories: https://arxiv.org/pdf/2511.04032
- InfoQ, session traces and cost controls for agent failures: https://www.infoq.com/news/2026/09/observability-ai-agents/
- Replit agent production-database deletion, July 2025: https://www.eweek.com/news/replit-ai-coding-assistant-failure/ and https://www.tomshardware.com/tech-industry/artificial-intelligence/ai-coding-platform-goes-rogue-during-code-freeze-and-deletes-entire-company-database-replit-ceo-apologizes-after-ai-engine-says-it-made-a-catastrophic-error-in-judgment-and-destroyed-all-production-data
- Instruqt, why developers don't trust marketing: https://instruqt.com/blog/why-developers-dont-trust-marketing-and-what-you-can-do-about-it
- Okoone, why developers don't buy the hype: https://www.okoone.com/spark/marketing-growth/why-software-developers-dont-buy-the-hype/
- Tech Audience Accelerator, hype words in technical copy: https://techaudienceaccelerator.substack.com/p/hype-words-are-killing-your-technical
- StateShift, marketing to developers without losing trust: https://blog.stateshift.com/?p=3427
- Markepear, launching a dev tool on Hacker News: https://www.markepear.dev/blog/dev-tool-hacker-news-launch
- daily.dev, Hacker News marketing for developer tools: https://business.daily.dev/resources/hacker-news-marketing-developer-tools-show-hn-launch-day-sustained-coverage/
- Lessons launching a developer tool on HN vs Product Hunt: https://medium.com/@baristaGeek/lessons-launching-a-developer-tool-on-hacker-news-vs-product-hunt-and-other-channels-27be8784338b
- Landing pages read for patterns only: https://sentry.io/ ; https://langfuse.com/ ; https://www.langchain.com/langsmith ; https://arize.com/phoenix ; https://www.helicone.ai/ ; https://www.honeycomb.io/ ; https://plausible.io/ ; https://tailscale.com/ ; https://linear.app/ ; https://posthog.com/ ; https://resend.com/ ; https://supabase.com/ ; https://vercel.com/
- Vendor comparisons (bias noted; used only for the complaint patterns: self-host cost, proxy overhead, licence nuance): https://www.helicone.ai/blog/best-langsmith-alternatives ; https://arize.com/resources/arize-alternatives/ ; https://www.morphllm.com/comparisons/helicone-vs-langsmith
