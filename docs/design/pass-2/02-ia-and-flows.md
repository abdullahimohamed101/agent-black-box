# Design pass 2, step 2: information architecture, flows and structure

Status: written 2026-10-16 from Prompt 1 sections 1, 2 and 8 (`docs/design/design-pass-2-prompts.md`), the audit and direction in
`01-direction.md`, ADR-166, and the route tree under `apps/web/src/app` (read on `main` at `250ecc7`, plus the `retention` page
on `feature/phase-19b-data-lifecycle`). Nothing here is invented: every route below exists in the tree or on that branch.

Product context (from the pipeline): Agent Black Box, a web app, desktop-first and usable at 375 px; developer-tools / AI-agent
observability; users are developers and AI engineers under time pressure, plus tech leads and security reviewers; the core problem
is trusting and debugging what agents did; key actions: understand a run and its first failure, compare runs, review findings and
approve or deny, set up alerts, keys and limits, manage retention, quotas and access.

---

## 1. Information architecture

### 1.1 Before: the real sitemap (from `apps/web/src/app`)

```text
/                                   entry: redirects to the first workspace's Overview, or "No workspaces yet", or the fixture entry page
/login                              sign-in card (?return_to=, ?error=login_failed|rate_limited|unavailable|not_configured|no_access|logout_failed)
/invite                             invitation acceptance (token in the URL fragment; states reading, no-token, signed-out, ready, unavailable)
not-found.tsx                       "This page isn't here" (also a non-member's view of a workspace: loadWorkspace -> notFound)
error.tsx                           "Something broke on this page" (error boundary, digest only)
/w/[workspace]/layout               session gate: unauthenticated -> /login?return_to=; not a member -> 404; API down -> "Workspace unavailable"
/w/[workspace]                      Projects list (a bare list of project links plus "All projects")
/w/[workspace]/projects/[project]   Overview ("Dashboard": 5 figures, Recent failures, Recent runs; the Setup guide when there are no runs)
  /runs                             Runs list (?q=, ?status=, ?agent=, ?range=; Needs attention; Compare mode; facets)
  /runs/[runId]                     Run detail (?view=waterfall|evidence, ?event=, ?finding=; replay mode; context column: Story, Agents, Findings, Evaluations; inspector)
  /runs/compare                     Compare (?left=, ?with=)
  /analytics                        Analytics (window toggle; figures, daily spend, by agent, by model, retry cost)
  /experiments                      Experiments list and creation form
  /experiments/[experimentId]       Experiment detail (variants, attach runs, evaluations)
/w/[workspace]/security             Security findings (facets: severity, detector, project, run started; ?project=; rail item needs run.read)
/w/[workspace]/approvals            Approvals (?state=pending|decided|all, ?approval=<id> opens the side panel; rail item needs approval.read)
/w/[workspace]/alerts               Alerts (?tab=rules|destinations|history; rail item needs alert.read)
/w/[workspace]/settings             redirect -> settings/members
  /settings/members                 Members and invitations
  /settings/api-keys                API keys (secret shown once)
  /settings/pricing                 Pricing overrides
  /settings/security                Capture mode, detector mutes, destination allowlist
  /settings/quotas                  Per-project quotas and overrides
  /settings/policies                Policy editor, dry run, publish, history and diff
  /settings/audit                   Audit log
  /settings/sessions                Your sessions
  /settings/retention               Retention (on feature/phase-19b-data-lifecycle; not on main yet)
/api/abb/[...path], /api/auth/*, /api/health   not screens (server routes)
```

Shell (every `/w/` page): rail (brand, workspace switcher, project switcher, 8 links, email, Sign out) and top bar (breadcrumb,
search field with `/`, Recording / Idle indicator). Cross-cutting states: loading spinner, empty, error with retry, forbidden
("Not available for your role"), "trace expired" and withheld-payload notices on the run page, "N new events" while live.

### 1.2 After: the sitemap

```text
PUBLIC
  /login                                  Sign in (unchanged route; same six fixed messages)
  /invite                                 Accept invitation (unchanged)
  /                                       redirect -> /w/{first}/projects/all/runs ; "No workspaces yet" page when no membership
  not-found, error, workspace-unavailable  (unchanged routes; same one-sentence pattern as every state)

SHELL (one top bar): Runs · Review · Analytics · Experiments · Settings     scope: workspace / project     search (/)     person menu

RUNS (home)
  /w/{ws}/projects/{p}/runs               Runs: a one-line 7-day summary, the search field with chips, facets, the list; the Setup guide when empty
  /w/{ws}/projects/{p}/runs/{id}          Run: three-line header; tabs Timeline | Waterfall | Evidence | Findings | Evaluations; Steps column; inspector; replay
  /w/{ws}/projects/{p}/runs/compare       Compare (unchanged route)

REVIEW (workspace-scoped; shown when the person has approval.read or run.read)
  /w/{ws}/approvals                       Review > Approvals (route kept; tab bar: Approvals · Security findings)
  /w/{ws}/security                        Review > Security findings (route kept; ?project= still pre-filters from a project)

ANALYTICS
  /w/{ws}/projects/{p}/analytics          unchanged route; the 7-day figures from the old Overview live here in full

EXPERIMENTS
  /w/{ws}/projects/{p}/experiments        unchanged
  /w/{ws}/projects/{p}/experiments/{id}   unchanged

SETTINGS (one page pattern; grouped left sub-nav)
  People      /settings/members · /settings/sessions
  Access      /settings/api-keys · /settings/policies · /settings/security
  Limits      /settings/quotas · /settings/retention · /settings/pricing
  Notify      /w/{ws}/alerts (?tab=rules|destinations|history; route kept, placed under Settings)
  Record      /settings/audit
  /w/{ws}/settings                        redirect -> members (unchanged)

REMOVED
  /w/{ws}                                 redirect -> runs (the project list is the scope control)
  /w/{ws}/projects/{p}                    redirect -> runs (Overview merged into Runs)
```

### 1.3 Every current route, exactly once

| Route | Decision | Reason |
| --- | --- | --- |
| `/` | Kept (redirect target changes to Runs) | entry logic is right; only the destination moves |
| `/login` | Kept | already one headline, one button, one sentence |
| `/invite` | Kept | its five states are already minimal; copy pass only |
| not-found / error / unavailable | Kept | pattern unified with every other state (one sentence, one action) |
| `/w/[workspace]` (Projects list) | Removed, redirects to Runs | a bare list that duplicates the project switcher (`Projects.tsx`) |
| `/w/[workspace]/projects/[project]` (Overview) | Merged into Runs | audit A.1: five figures with no question behind them, failures listed twice; the figures become one sentence above the list, the full set stays on Analytics |
| `/runs` | Kept (home) | the list is the product's table of contents |
| `/runs/[runId]` | Kept, restructured | audit A.3; the first-error flow is the core action |
| `/runs/compare` | Kept | already one headline and one table; heavier only in its ids |
| `/analytics` | Kept | receives the 7-day figures block from Overview |
| `/experiments`, `/experiments/[id]` | Kept | restyled on the shared table and form rules only |
| `/security` | Merged into Review (route kept) | audit A.0: one of three workspace queues with its own tab style; a reviewer's queue beside Approvals |
| `/approvals` | Merged into Review (route kept) | same |
| `/alerts` | Moved under Settings (route kept) | audit A.4: it is configuration (rules, destinations) with a log; it sits with API keys and quotas |
| `/settings` redirect | Kept | |
| `/settings/members` … `/settings/sessions` (8) | Kept, one pattern | audit A.5: nine layouts become one |
| `/settings/retention` (19b branch) | Kept, one pattern | lands with Phase 19b; designed here so it is built on the pattern from the start |
| `/api/*` | Not screens | |

Top-level items: 8 today (owner) to 5. Second-level tab bars: 4 styles to 1 component (Review tabs, run view tabs, Alerts tabs,
Settings sub-nav are the same component in two orientations).

### 1.4 Navigation structure and recommendation

**Recommendation: one top bar; no rail.** Reasons, from the audit:

1. The rail costs 224 px on every screen (A.0) and on the run page that width is what the timeline loses when the inspector opens
   (`docs/screenshots/phase-21/README.md`: the context column folds at 1408 px to keep the pane above 480 px). A bar costs no
   height the header stack does not already pay: today's top bar is 44 px and stays 44 px.
2. With five items the bar fits at 1440 and 1024 with room for the scope control; the rail only earned its column with eight
   items and two switcher boxes.
3. "Where am I" is shown once: the bar's current item is the page; the page title line under it replaces the breadcrumb for
   everything except a run and an experiment (two levels: "Runs / name").
4. Below 900 px the five links become a "Menu" button at the left of the bar that opens the same list; the scope control stays in
   the bar; search becomes an icon. This replaces today's three stacked chrome rows on a phone (A.0: four bands, headline at 230 px)
   with one 44 px bar.

Alternatives and why not: a lighter nav-only rail (keeps width cost; easier revert, so the nav stays one component); a bottom
tab bar on phones (the product is desktop-first and the phone case is "read a failure on the way to the laptop"; a persistent
bottom bar would cover the fault line on the first screen of a 375 run page). Both recorded in ADR-166.

Bar anatomy, left to right: brand glyph and name (link to Runs) · Runs · Review (with a pending count when > 0) · Analytics ·
Experiments · Settings · (spacer) · scope control "demo / All projects ▾" (one disclosure listing workspaces and this workspace's
projects; opens Runs in the new scope) · search button "⌕ /" (opens the existing top-bar listbox) · person menu (email, role,
Sign out as a POST). The recording indicator becomes a dot with the accessible name "Recording" beside the Runs item only while
something is RUNNING in scope; no word.

What is merged, demoted or hidden behind progressive disclosure:

| Thing | Where it goes |
| --- | --- |
| Workspace and project switchers (two boxes) | one scope control in the bar; the project list page is gone |
| Breadcrumb | the page title line; two levels only on a run or an experiment |
| Recording / Idle word | a dot with an accessible name; the word only as a tooltip |
| Email and Sign out in the rail foot | person menu |
| Overview figures | one sentence above the runs list; full figures on Analytics |
| Approvals, Security | tabs of Review |
| Alerts | Settings > Notify |
| Run identity (id, model, ordering, started timestamp) | "Details" disclosure under the figures line |
| Run figures beyond five | the same Details |
| Health dimensions | removed from the UI (API unchanged) |
| Agents section | the waterfall's agent tree (already there) |
| Findings, Evaluations sections | tabs on the run page |
| Keyboard shortcuts | tooltips and a `?` sheet |
| Event-class chips, Collapse groups | the Filter menu |
| Status toggles, agent field, started select on Runs | the search field's grammar and the facets |

---

## 2. User flows

Notation: `[screen]` a screen, `→` a step, `?` a decision, `✓` success, `✗` error or edge. Every screen named exists in 1.2.

### 2.1 Onboarding: first sign-in and the setup guide

```text
[/login] "Sign in to see what your agents did" [Sign in]
  → identity provider (outside the app)
  → /api/auth/callback
      ? callback failed ........... ✗ [/login?error=login_failed] "Sign-in did not complete. Try again."  [Sign in]
      ? too many attempts ......... ✗ [/login?error=rate_limited] "Too many sign-in attempts. Wait a minute and try again."
      ? provider down ............. ✗ [/login?error=unavailable]
      ? not configured ............ ✗ [/login?error=not_configured] (operator problem; no button)
      ? email has no invitation ... ✗ [/login?error=no_access] "No access yet. Ask a workspace owner to invite you." (ADR-157: no user row)
      ? ok, no membership ......... ✗ [/] "No workspaces yet" + Sign out
      ? ok, member ................ ✓ [/] → redirect [Runs] of the first workspace (or ?return_to=)
[Runs], first time (0 runs in scope)
  → the Setup guide replaces the list (not a separate page): display headline "No runs yet", three steps
      1 Create an API key → ? can api_key.create ............ link to [Settings > API keys] (secret shown once: [Copy], "shown only once" sentence)
                           ? cannot .......................... "Ask a workspace owner or admin for a key" + link
      2 Install the SDK   [pip install agent-black-box] [Copy]  (✗ copy blocked → "Could not copy; select the text")
      3 Record a run      env vars + a three-line snippet [Copy]
  → "Waiting for your first event" (the page already polls the list)
      ? first run arrives .......... ✓ "Your first run arrived." [Open the run] [Show all runs]
      ? API unreachable ............ ✗ the standard error state replaces the guide: "Could not load this data" [Try again]
Invitation path: [/invite#token] → ? signed out → "Sign in to accept" → provider → back to [/invite] (token kept in sessionStorage)
                                     ? no token → "This invitation link is incomplete" (fixed copy)
                                     ? accepted ✓ → [Runs] of that workspace
                                     ? expired or revoked ✗ → the API's fixed message as text, "Ask for a new invitation"
```

Screens touched: Login, provider, Runs (guide), Settings > API keys, Run. Decision points: 7 sign-in outcomes, key permission,
first event. Edge: a person who is a member of several workspaces lands in the first; the scope control is how they change it.

### 2.2 Core action: open a failed run and find the first error

```text
Entry A: [Runs] → the list is newest-first; failed rows carry the red miniature dash + "Failed at step 8 · 1 retry"
Entry B: an alert (Slack, webhook) → run link → [Run]
Entry C: search (/) → type → ↵ opens [Run] or hands the text to [Runs]
Entry D: [Review > Security findings] → "Show evidence" → [Run?finding=<id>]

[Run], failed
  header line 1: "Runs / Fix flaky checkout test"      right: "Failed · 5d ago"  (the one red word)
  header line 2: "Failed at step 8 after 6.5 s: the test command exited 1."  [Go to first error  e]
  header line 3: "1 retry · 8.8 s · $0.0100 · 1,700 in / 160 out · 3 errors"   Details ▾
  tabs: Timeline 12 | Waterfall | Evidence | Findings 2 | Evaluations
  Steps column (left): numbered, the failing steps marked ✕ in ink
  Timeline: the fault line (the only red) above the first error row, label "first error · step 8"
  ? the first error is below the fold ...... the line draws when it scrolls into view; [Go to first error] scrolls to it and
                                             lands the 600 ms outline; focus moves to the row (keyboard `e`)
  → click the row → inspector opens (slides 8 px): event type, "What went wrong" (exit code, stderr excerpt), payload sections,
    [Show evidence] links, [Open in waterfall]
  ? the reader wants the cause, not the symptom → [Findings 2] tab: "the run failed: a command failed" (Critical, by weight)
                                                   and "Probable: a code change was followed by failing tests" → [Show evidence]
  ? role cannot read payloads (VIEWER, BILLING) ... the detail line ends "· details withheld for your role"; the inspector shows
                                                   the withheld notice (one sentence, no bar); evidence links still work on metadata
  ? the run is still RUNNING .................... header line 2 reads "Running · step 6 · last event 2 s ago" with the accent dot;
                                                   no red anywhere; rows arrive at the bottom; scrolled away → "N new events" control
  ? the run is WAITING_FOR_APPROVAL ............. line 2 "Waiting for approval: github.push" (caution word) + [Open Review]
  ? trace expired (retention) ................... line 2 in ink: "Events for this run were removed after N days"; figures and
                                                   findings stay (derived records); tabs that need events show the same sentence
  ? stream drops ................................ "Reconnecting" in the figures line (caution); rows stay; nothing moves
  ? run not found / not yours ................... [not-found] "This page isn't here" [Go to your runs]
  ? API error ................................... error state in place of the header, [Try again]; the shell stays
Exit: back to [Runs] via the bar or the title line; or [Compare] from the list; or [Review] if an approval is waiting.
```

Screens touched: Runs, Run (Timeline, Findings, inspector), Review. Decision points: 9. The first screen of a failed run must show,
at 1440 and at 375, the headline sentence and the red line without scrolling (step 3 wireframes enforce this).

### 2.3 Compare runs

```text
[Runs] → [Compare] (a toggle in the list head; aria-pressed) → each row gains a checkbox; the head reads "Pick two runs"
  ? one picked ............ "Pick one more"
  ? two picked ............ [Compare these] enabled → [Compare?left=&with=]
[Compare]
  title "Compare runs"; one sentence: "The runs diverged at step 22: A read app/session.py, B read app/oauth.py."
  deltas line (right minus left): duration, cost, calls, retries, files
  A / B header row (names link to each run; status words; the failed one red)
  Steps table: [Show the 18 earlier steps] disclosure; Jump to divergence · Previous · Next (keys n / p)
  Summary (measures) ▾ · Evaluations · Cost by model · Files
  ? neither run has steps in common ..... "These runs share no steps" + both still listed
  ? a side is withheld for the role ..... the existing metadata notice, one sentence
  ? one run id invalid .................. error state, [Back to runs]
  ? same run twice ...................... the list refuses the second pick ("Pick a different run")
Exit: [Runs] or either run.
```

### 2.4 Approve an action

```text
Entry: the bar's "Review 3" count; an alert; the run's "Waiting for approval" line → [Open Review]; or directly [/approvals]
[Review > Approvals], Pending tab (Decided · All as the other tabs; ?state= kept)
  list: capability (mono), resource (scrubbed), risk (shield by weight: R4 filled, R3 caution outline, below muted), state word,
        requested, time left (server seconds_left aged locally, never negative, "Expired" at zero), policy vN
  → click a row → the request panel (?approval=<id>; a side panel at ≥1024, the whole screen at 375 with [Back])
      intent as the API returns it; arguments as formatted JSON text; "Approver needed: Admin or above"; expiry sentence
      [Approve] [Deny] + optional comment (1,000 characters, counter)
      → confirm step: "Approve github.push on origin main?" [Approve] [Cancel]  (a second click, not a modal dialog)
      ? ✓ decided ............... the row moves to Decided; the panel shows the outcome word and who decided; focus to the panel's heading
      ? expired meanwhile ....... the API's fixed message as one sentence in the panel; the row reads "Expired"
      ? already decided by someone else ... same pattern ("Already approved by …")
      ? role may read but not decide ...... buttons absent; sentence "Your role can read requests but not decide them"
      ? conflict / network ................ one sentence + [Try again]; nothing optimistic
  ? no pending ................... "Nothing is waiting for you." (one sentence; no illustration)
Exit: the run link in the panel; [Runs].
```

### 2.5 Set up an alert

```text
[Settings > Notify > Alerts] (route /w/{ws}/alerts; tabs Rules · Destinations · History)
  ? no destination yet → the Rules tab's primary action is [Add a destination] with the sentence "A rule needs somewhere to send to."
  Destinations: [Add a destination] → inline form on the page (type: Webhook | Slack | Email; name; URL or address; secret)
      ? port not allowed (ADR-158) ...... field error from the API's fixed reason: "Only port 443 is allowed" (step 5 writes the copy)
      ? address refused (SSRF guard) .... field error, one sentence
      ✓ saved → [Send a test] → "Test sent" / "Test failed: <fixed code>" as one line under the row; nothing stored
  Rules: [Add a rule] → inline form: name · when (run fails | security finding | approval needs attention | spend over a limit)
         · only if (project, agent, status set, severity) · sends to (checkbox list of destinations) · cooldown
      ? no destination picked ........... field error "Pick at least one destination"
      ✓ saved → the rule row shows On, "Never fired"
  History: one table (when, rule, destination, outcome word, attempts); a failing destination reads "failing" in caution text and links to it
  ? role cannot manage (alert.read only) → forms absent; sentence "Your role can read alerts but not change them"
Exit: back to Runs via the bar.
```

### 2.6 Error recovery

```text
Session expired (API 401 on any request)
  → the client redirects to [/login?return_to=<here>] (existing client.ts behaviour)
  → sign in → ✓ back to the exact page (filters, ?view=, ?approval= all in the URL, so nothing is lost)
  → the run page's live stream: "unauthorized" state shows "Signed out" in the figures line until the redirect lands (existing)
Forbidden (403)
  → read of a page: "Not available for your role" (one sentence, no bar, no illustration); the bar still works
  → an action: the sentence "Your role does not allow this." under the control, the API's message as text, request id in small
  → search on content fields (ADR-101): "Your role cannot search file: or command:. Metadata search still works."
Offline / API unreachable
  → page load: "Could not load this data" [Try again] (ErrorState), in place of the content, never a blank page
  → run page live: "Reconnecting" in the figures line; rows kept; when back, the gap is merged (live.ts); no toast
  → a write: one sentence "The request failed." [Try again]; the form keeps its values
  → workspace layout cannot reach the API: "Workspace unavailable" page, one sentence, "Reload to try again"
Not found (404): [not-found] one sentence + [Go to your runs]; a non-member sees the same page (no existence leak)
Quota or rate limit refused (ADR-154, ADR-152): the API's fixed code becomes one sentence naming the limit and when it resets
Trace expired: see 2.2
Rendering error: [error] "Something broke on this page. Nothing you recorded was lost." [Try again] [Go to your runs] (digest only)
```

### 2.7 Settings and profile

```text
[Settings] → redirect → [Members]   (sub-nav: People · Access · Limits · Notify · Record; tabs the role may not read are absent)
Members:  table (member, role select, joined, [Remove] in caution text); Invitations section: email + role → [Create invitation]
          → the link shown once with [Copy] ("shown only once"); open invitations listed with [Revoke]
          ? remove the last owner → refused by the API; the sentence under the row
Sessions: your sessions; the current one marked, not revocable; [Sign out that session] in caution text
API keys: [Create a key] → name, project, scopes → the secret shown once [Copy]; keys table with [Revoke]
Policies: scope select; editor (line numbers, API validation with line:column issues that move the caret); [Dry run] → result
          section; [Publish] with comment; history with diff; unsaved-changes guard on navigation
Security: capture mode (radios with plain sentences), detector mutes (checkboxes), destination allowlist (list + add field)
          → [Save] shows what the API stored, never optimistic
Quotas:   project select; one row per quota: name, "41,200 of 50,000 today (82 %)", a 4 px bar, state word (Near limit in caution);
          the override field in the same row; [Save limits] at the end of the section
Retention (19b): the same row pattern: per class the days, the default, the override field; one sentence on what a purge does
Pricing:  table of model prices and overrides; add row inline
Alerts:   see 2.5
Audit:    one table, newest first, filters as chips (actor, action, when); [Load more]
Profile:  there is no profile page; the person menu shows email and role, and Sign out (POST)
Every settings action: ✓ a one-line confirmation under the section ("Saved"), ✗ one sentence + request id; 403 → "Your role does not allow this."
```

---

## 8. Structural recommendations

### 8.1 Navigation pattern

One top bar (1.4). Content zones under it are the same on every page: a title line (h1, right-aligned status or primary action),
an optional one-sentence lede, an optional tab row, the work. The run page is the one page whose work area fills the viewport
height (as today, `main.app-page`); everything else scrolls as a document.

### 8.2 Screens likely to cause confusion or drop-off, and the fix

| Screen | Why it confuses today (audit) | Fix |
| --- | --- | --- |
| Overview | Lands the person on figures they did not ask for; failures appear twice; nothing says what to do | Removed; Runs opens on the newest runs with failures marked by the one red mark |
| Runs list | Three ways to filter; 17 controls before the data; status in five hues | One field + facets; status by word; "Needs attention" a one-click facet |
| Run page, first screen | Six bands before the first row; the red headline competes with the red rows and the red line | Three header lines; the line is the only red; the headline sentence is ink |
| Run page, context column | Seven sections on a healthy run; cards inside a column; a "Health: Bad" line beside a Critical finding beside a red headline | Steps only; Findings a tab with a count; Health removed |
| Security vs Settings > Security | Two things named Security in the rail and the settings tabs | "Security findings" under Review; "Capture and detectors" under Settings > Access |
| Approvals detail at 375 | The list steps aside and the panel takes the screen with Close at the top right | Kept, but with a [Back] at the top left and the heading focused |
| Settings | Nine different layouts; cards on Quotas; a form that appears under the cards | One pattern; the override field sits in the quota's own row |
| Phone chrome | Four stacked bands before content | One bar with a Menu button |
| Compare | Long ids in the A/B header | Names first, ids in mono small under them |
| Search help | A disclosure beside the field that opens a grammar table | A `?` link in the field's empty state; the grammar table in the listbox's footer |

### 8.3 Flows simplified or merged

- Overview + Runs → Runs (one landing, one list, one sentence of figures).
- Approvals + Security → Review (one list style, one tab bar, one count in the bar).
- Alerts → Settings (configuration beside configuration).
- Three run-list filters → one grammar surfaced two ways (typing, facet clicks that write chips).
- Context column sections → Steps column + two tabs.
- Projects list page → the scope control.
- Four tab components → one.
- Approve: confirm as a second click in place, not a dialog.

### 8.4 Progressive disclosure

| Shown first | Behind one click |
| --- | --- |
| Run: name, status, age, the headline sentence, five figures | Details: id, agent, model, ordering, started timestamp, tokens split, tool calls, files changed |
| Timeline rows | Inspector: payloads, evidence, "what went wrong" |
| Findings count in the tab | the findings list; each finding's Details |
| Quota row: usage and state | the override field is in the row, but the "what a limit does" sentence is one lede, not per card |
| Search field | grammar help in the listbox footer; chips from the server's parse |
| Scope control: current workspace / project | the list of both |
| Person menu: avatarless email | role, Sign out |
| Keyboard: nothing drawn | tooltips name the key; `?` opens the sheet |
| Compare: the divergence sentence and the changed steps | "Show the N earlier steps"; measures |
| Policies: the editor | history and diff below a disclosure |

### 8.5 Accessibility notes per screen

Global: one `nav[aria-label="Primary"]`; `aria-current="page"` on the bar's item; skip link stays; focus ring 2 px accent, 2 px
offset, never removed; every status is a glyph plus a word (never colour alone); the fault line has a text label in the DOM
("first error · step 8") and the row carries `aria-describedby` to it; all text pairs at least 4.5:1 in both schemes (step 4 computes
AAA 7:1 for body on canvas); targets 24 px minimum, 44 px on phones; zero motion under `prefers-reduced-motion`; untrusted text
is a text node, never HTML (repo rule 5).

| Screen | Notes |
| --- | --- |
| Login | `main` is the only landmark; the one button is the first tab stop; the error is `role=alert` |
| Runs | the summary sentence is `role=status` while refetching; the search field is a combobox with the listbox; facets are `aria-pressed` buttons that also write the chip (one source of truth); the table has a visible caption count; Compare mode announces "Pick two runs" |
| Run | the title line is the h1; the headline sentence is `data-testid=headline` and not a heading; the figures are a `dl`; tabs are a tablist with panels (or links with `aria-current` where panels are routed); the Steps column is a list of buttons; the timeline keeps its roving `tabindex` and keys (j/k/↵/e/Esc) with the keys named in tooltips, not drawn; the inspector is `aria-labelledby` its event type and `inert` while closing; live rows are announced through the existing "N new events" control, not a live region per row |
| Compare | the divergence sentence is `role=status`; the steps table has a row header on the step number; n/p keys named in a visually hidden paragraph |
| Review > Approvals | pending count in the bar is text inside the link ("Review, 3 pending"); the panel is a `section` with its heading focused on open; the confirm step is two buttons, not a dialog; time left is `aria-live=off` (it ticks) |
| Review > Security findings | severity is a glyph and a word; the facet column is `aria-label="Narrow the results"`; "Show evidence" is a link with the finding's name |
| Alerts | On toggles are switches with the rule name; Delete is a button with the rule name, confirm as a second click |
| Settings (all) | the sub-nav is `nav[aria-label="Settings"]`; each section is a `section` with an h2; label and control in one `label`; errors `role=alert` under the field and `aria-describedby`; "Saved" is `role=status` |
| Settings > Policies | the editor is a textarea with a described line-number gutter (`aria-hidden`); issues list is a list of buttons that move the caret |
| No access / not found / error | h1 and one sentence; the action is a link; no `role=alert` on not-found (it is the page, not an event) |

### 8.6 List of removals (for the step 3 hand-off)

Removed as screens: Projects list (`/w/{ws}`), Overview. Removed as navigation: the rail, two switcher boxes, rail foot, breadcrumb
(replaced by the title line), the Recording word, three of four tab styles. Removed as page furniture: stats strip, second runs
table, status toggle row, agent field, started select, "Needs attention" button, event-class chips, Collapse groups button,
health dimensions, Agents section, finding cards, drawn key caps, quota cards, routine illustrations, tone bars, filled chips,
green. Kept routes with new placement: `/approvals`, `/security`, `/alerts`.
