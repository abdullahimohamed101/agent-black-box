# Design pass 2, step 5: microcopy, errors, empty states and loading

Status: batch 5a written 2026-10-16 (shell, navigation, sign-in and no-access pages, the Runs home); batch 5b written 2026-10-16 (sections 5
and 6: the run page and Compare); batch 5c written 2026-10-16 (sections 7 to 9: Review, the Settings pattern and its tabs, Alerts). Later batches append their sections below (5d Analytics, Experiments). Source prompt: Prompt 1 sections 4 to 7
(`docs/design/design-pass-2-prompts.md`). Voice from `01-direction.md` (voice cues): sentences, not labels; one verb per button;
numbers carry units; no upper case shouting; no marketing tone; never echo captured content (names of runs, commands, payload
text, search text). A string that includes a run, agent or workspace name renders it as a text node and appears only where the
person already sees that name.

The strings live in typed modules, one per area, in `apps/web/src/copy/` (`shell.ts`, `auth.ts`, `runs.ts`, `states.ts`, and from 5b `run.ts` for the run page and Compare). A
component and its test import the same constant, so a copy change is one edit.

Vocabulary rules used below: "run" not "trace" for the thing in the list; "scope" is the workspace plus project pair; "Review" is
the queue of things that wait for a person; error sentences say what happened, then what to do; no "oops", no "please", no
exclamation marks, no apology.

---

## 1. The frame (top bar, navigation, search, scope, person)

| Element | Copy | Notes |
| --- | --- | --- |
| Skip link | Skip to content | first tab stop, visible on focus |
| Brand link | Agent Black Box | goes to Runs; the name is hidden below 600 px, the link keeps its accessible name |
| Primary navigation label | Primary | the only `nav` with this name |
| Items | Runs, Review, Analytics, Experiments, Settings | one word each; Settings only for a signed-in person; Review only with run or approval read |
| Review count | `3` beside the word; screen readers hear "Review, 3 waiting" | text, not a badge; hidden at zero |
| Recording | a dot beside Runs, screen-reader text "Recording" | only while a run is running in scope; no word on screen, nothing when idle |
| Menu button (below 900 px) | Menu | opens the five links; Escape closes and returns focus to it |
| Scope button | `demo / All projects` | tooltip "Change workspace or project"; opens a list of workspaces (when there is more than one) and of projects |
| Scope panel headings | Workspace, Project | small, ink-2, sentence case |
| Scope, no projects | No projects yet. They appear once an agent reports here. | |
| Search button | label "Search runs", tooltip "Search runs (press /)" | opens the field in place of the bar |
| Search field | label "Search all runs", placeholder "Search runs" | existing combobox |
| Search results footer | Show all in the runs list | |
| Search states | Searching… / No runs match this search. | the error sentences are unchanged from the existing component (they never repeat the search text) |
| Person button | the email; "Account" when there is no person (fixture data) | |
| Person panel | Signed in as, the email, "Role: Owner", link Sessions, button Sign out (step 5c removed the temporary Security findings and Alerts links); with fixture data and no person: "Fixture data: no one is signed in." | |
| Sign-out failure | Sign-out failed; you may still be signed in. | `role=alert`, unchanged |

Page title line (replaces the breadcrumb): the h1 is the only "where am I". The Runs page title is `Runs`.

## 2. Sign-in, invitation and the pages outside the shell

| Screen | Headline (display) | Supporting copy | Action |
| --- | --- | --- | --- |
| Sign in | Sign in to see what your agents did | You sign in with your organization's identity provider. | **Sign in** (the one filled button) |
| Sign in, fixtures | the same | Fixture mode: no sign-in is needed. | Continue |
| Invitation, reading | Join a workspace | Checking your invitation… | none |
| Invitation, signed out | Join a workspace | An invitation to a workspace is waiting for you. Sign in with the email address the invitation was sent to, then accept it. | Sign in |
| Invitation, ready | Join a workspace | An invitation to a workspace is waiting for you. You are signed in as `email`. The invitation only works for the email address it was sent to. | Accept invitation (Accepting… while busy) |
| Invitation, no link | Join a workspace | This page needs an invitation link from a workspace owner. Open the full link you were sent. | none |
| Invitation, unavailable | Join a workspace | The service is unavailable. Reload to try again. | none |
| No workspaces | No workspaces yet | You are signed in, but you are not a member of any workspace. Ask a workspace owner for an invitation link, then open it. | Sign out |
| Workspace unavailable | Workspace unavailable | The API answered 503. Reload to try again. (the first sentence is the server's fixed text) | none |
| Not found | This page isn't here | The link may be old, or the run or project may have been removed. | Go to your runs |
| Rendering error | Something broke on this page | Nothing you recorded was lost. Try again, and if it keeps happening tell whoever runs this workspace (reference `digest`). | Try again, Go to your runs |

Sign-in notices (the six fixed sentences; the query value is never shown, and what the page reveals is unchanged, ADR-157):
login_failed "Sign-in did not complete. Try again."; rate_limited "Too many sign-in attempts. Wait a minute and try again.";
unavailable "The sign-in service is unavailable. Try again shortly."; not_configured "Sign-in is not configured on this server.";
no_access "No access yet. Ask a workspace owner to invite you."; logout_failed "Signing out failed; your session may still be active."

Session expired is not a screen of its own: the client sends the person to `/login?return_to=<here>` and, after sign-in, back to the
same page (filters are in the URL, so nothing is lost). The sign-in page shows no extra sentence for it (a seventh message would
tell a visitor something the page does not tell today).

## 3. Runs (home)

### 3.1 Page

| Element | Copy |
| --- | --- |
| Title (h1) | Runs |
| Compare toggle | Compare (pressed state; no other wording) |
| 7-day sentence | `Last 7 days: 12 runs, 83% succeeded, 3 failed, $22.15 spent, 2 in progress.` then a link **Analytics** |
| Sentence at 0 failures | `Last 7 days: 12 runs, 100% succeeded, none failed, $22.15 spent.` (zero is said in words) |
| Sentence on a phone (< 600 px) | `Last 7 days: 12 runs, 3 failed.` (the success rate, spend and in-progress clauses give way) |
| Sentence while refreshing | the old sentence stays; a polite status "Updating…" |
| Sentence when there are no runs in the window | not shown (the setup guide or the empty state speaks instead) |
| Search field | label "Search runs"; placeholder `Search runs, for example status:failed`; a **?** button named "Search help" |
| Search help panel | Terms are combined with AND. Prefix a term with - to exclude it, quote phrases, end a value with * for a prefix. Times can be relative, for example started:>=-24h. Then the list of searchable fields from the API |
| Chips | the typed text of each term with a **Remove** button named "Remove status:failed"; the "Needs attention" preset is the chip `status: failed, timed out, blocked` |
| Count and reset | `13 runs loaded (end of list)` / `50 runs loaded` and a **Clear filters** button (only when something is filtered) |
| Facet groups | Needs attention (a one-click preset at the top), Status, Agent, Model, Started (Last hour, Last 24 hours, Last 7 days), Errors and retries (Has errors, Retried), More fields (Tools, Event types) |
| Facet column on a narrow screen | a disclosure "Narrow results (2 on)", open by default at 600 px and up, closed on a phone |
| Table heads (sentence case) | Status, Run, Agent, Started, Duration, Cost, Findings (Matched only when the search names indexed fields) |
| Failure line | `Failed at step 7 · 2 retries · $0.0167 spent after` (the miniature red dash comes before the sentence; the words are unchanged from the existing component) |
| Row details | the existing "Show details of NAME" disclosure |
| Pagination | Load more / Loading… |

### 3.2 Empty states

| State | Headline | Body | Action |
| --- | --- | --- | --- |
| First time: no runs in this scope (the setup guide replaces the list) | No runs yet | Record one agent run in three steps. This page switches to it when the first event arrives. | the three steps below |
| Zero results for a filter | No runs match these filters | Try clearing a filter. | none of its own: the "Clear filters" button in the chip row above is the action (one reset control, not two) |
| Zero results from the top-bar search | No runs match this search. | | Show all in the runs list stays as the footer option |

The setup guide keeps its three steps and behaviour (restyled, no serif):

1. **Create an API key.** Allowed: "Create a key in settings and copy it at once: the token is shown only once." Not allowed: "Ask a workspace owner or admin for a key (API keys). The token is shown only once, when it is created."
2. **Install the SDK.** `pip install agent-black-box` with a **Copy** button ("Copied" for 2.5 s; "Could not copy; select the text" if the clipboard refuses). Under it: "Working from a checkout of this repository? Run `pip install ./packages/sdk-python`."
3. **Record a run.** "Set `BLACKBOX_API_KEY` to your key (and `BLACKBOX_ENDPOINT` if the API is not on `http://localhost:8000`), then run:" and the three-line snippet with **Copy**.

Waiting line: "Waiting for your first event" (a dot in the accent colour). Arrival: "Your first run arrived." with **Open the run** and **Show all runs**.

### 3.3 Loading and transitions

| Moment | Copy |
| --- | --- |
| First load | Loading runs… (polite status, spinner dot; no skeleton, rows are 32 px and appear at once) |
| Filter or project change | the previous rows stay, dimmed; "Updating…" is announced; no spinner over the list |
| Load more | the button reads "Loading…" and is disabled |
| Compare mode on | "Tick 2 runs to compare them." then "1 of 2 runs picked." then **Compare the two runs**; "Pick one more run." when one is ticked |
| New runs arriving (the list polls) | rows simply appear; no toast, no animation |

## 4. Errors (shared by every screen)

One pattern: a headline, one sentence of what happened, one action. The API's own fixed message is shown as the sentence when it
sends one; the request id is shown in small type so a person can quote it. Nothing here repeats what the person typed.

| Situation | Headline | Body | Action |
| --- | --- | --- | --- |
| Session expired (401) | You have been signed out | Sign in again to pick up where you left off. | Sign in (link to `/login?return_to=` the current page) |
| Forbidden (403) | Not available for your role | Ask a workspace owner if you need access. (the API's message when it sends one) | none |
| Offline or the API is down (network, 502, 503, 504) | Could not load this data | The API could not be reached. (the API's fixed sentence) (request `id`) | Try again |
| Rate limited (429) | Too many requests | Wait N seconds and try again. ("Wait a moment and try again." with no `Retry-After`) | Try again |
| Server error (500) | Could not load this data | the API's fixed sentence (request `id`) | Try again |
| Not found (404) | Not found | the API's sentence; for a missing run: "This run no longer exists. It may have been removed by the retention policy, or the link may be wrong." | none |
| Trace expired | Trace expired | The trace of this run was removed by the retention policy; the run summary and its numbers remain. A comparison or an evaluation needs the trace. | none |
| Search refused or failed (Runs) | This search could not be understood / Your role cannot search that field / This search is too long / Search is busy / This search took too long | unchanged from the existing component, none repeats the search text | Try again for busy and timeout |
| Sign-out failed | | Sign-out failed; you may still be signed in. | none |

Notices (a sentence with a leading glyph, no bar, no fill) use the same words as before; only the treatment changed.

---

## 5. Run page (batch 5b)

Strings: `apps/web/src/copy/run.ts` (`run`). The headline sentences are composed in `apps/web/src/lib/runHeadline.ts` from the
same facts as before (Phase 22: the stored first error, SUMMARY_VERSION 6); they use event metadata only (tool, model, error
type, event kind, counts, cost), never content, so a viewer reads the same sentence as an owner.

### 5.1 Title line, headline, figures

| Element | Copy | Notes |
| --- | --- | --- |
| Back link | `Runs` then a `/` | accessible name "Back to runs"; the first level of the title line (KI-276); at 375 it is its own 32 px line with a leading chevron |
| Title (h1) | the run's name, else its id | 24/30 600 ink; 20/26 below 900 px |
| Status and age | glyph and word, then `· 5d ago`; a running run reads `Running · started 2 s ago` | the word is the only red thing here (Failed, Timed out, Blocked); the glyph is decoration |
| Replay | `Replaying · state at the playhead` replaces the status | unchanged words (`STATUS_AS_OF`) |
| Go button | **Go to first error** (its accessible name was "Show it" before 5b); tooltip "Jump to the first error (e)" | outline accent, right of the headline; full width and 44 px tall below 600 px; only when the run has a first error |
| Replay button | **Replay**, tooltip "Replay this run (r)" | in the tab row's right group; no drawn key cap |
| Five figures | Duration, Cost, Tokens (`1,700 in / 160 out`), Retries, Errors | one wrapping line, mono values, ink-2 labels, no red and no amber; in replay Duration reads Elapsed and every figure is the state at the playhead |
| Details | a disclosure button (`Details`, `aria-expanded`) at the right end of the figures line | opens in place; a `dl` with Run id, Agent, Model, Ordered by, Started, Model calls, Tool calls, Files changed, Agents (when more than one) and, below 600 px, Tokens, Retries and the "After the first error" line (the second line of the headline moves here so the headline and the line stay in the first screen); a withheld first error adds "details withheld for your role" |

### 5.2 The headline sentence

One sentence, ink, 600, 24/30 (20/26 below 900 px), never red, ended with a full stop. `N` is the step of the first error, `T` the time from
the run's first event to it. The second line (small, ink-2) keeps the Phase 22 facts: retries, time and cost spent after the
first error, the total.

| Status | Pattern | Example |
| --- | --- | --- |
| Failed, first error known | `Failed at step N after T: SUBJECT[, ERROR TYPE].` | Failed at step 8 after 6.5 s: shell command completed, error. |
| Failed, first error not located | `Failed after DURATION.` | Failed after 8.8 s. |
| Timed out | `Timed out at step N after T: SUBJECT.` / `Timed out after DURATION.` | Timed out at step 14 after 30 s: the run's end. |
| Blocked | `Blocked at step N after T: SUBJECT.` / `Blocked after DURATION.` | Blocked at step 9 after 2.1 s: policy action blocked. |
| Succeeded after a retry or an error | `Succeeded after N retries.` / `Succeeded after recovering from an error.` | Succeeded after 2 retries. |
| Succeeded | `Completed in DURATION for COST.` | Completed in 8.8 s for $0.0100. |
| Running | `Running at step N, last event 2 s ago.` (`Queued at step N.`; before any event `Running.`) | Running at step 6, last event 2 s ago. |
| Waiting for approval | `Waiting for approval of CAPABILITY at step N.` (`Waiting for approval at step N.` when the capability is unknown) | Waiting for approval of github.push at step 5. |
| Waiting (other) | `Waiting on SUBJECT at step N.` | Waiting on tool call at step 12. |
| Cancelled | `Cancelled at step N after DURATION.` | Cancelled at step 9 after 6.5 s. |
| Replay, before the first error | `Running at step N, replay.` (the outcome is never given away) | Running at step 4, replay. |
| Replay, first error reached | `First error at step N after T: SUBJECT.` (`Blocked at step N after T: SUBJECT.` for a policy block) | First error at step 8 after 6.5 s: shell command completed, error. |

Headline states that are not sentences: loading holds the frame with two bars and a polite "Loading the run summary"; error says
"Could not load the details of this failure." (recovery, running, waiting, success and cancelled variants as before) with **Try
again**; withheld adds " · details withheld for your role" to the second line.

### 5.3 Tabs

One tab component, `role="tablist"` named "Run views": **Timeline** (count of events), **Waterfall**, **Evidence** (count),
**Findings** (count), **Evaluations**. Counts are decoration (`aria-hidden`), so a tab's name is its plain label. Left and right
arrows, Home and End move and select; the URL keeps `?view=` (`waterfall`, `evidence`, `findings`, `evaluations`; the timeline is
the absence of the parameter). Old links keep working: `?finding=ID` opens Findings with that finding marked.

Right of the tabs: **Filter**, **Errors only** (timeline only) and **Replay**; **Collapse groups** (then **Expand groups**) is the
last item of the Filter popover. On the Waterfall, Evidence, Findings and Evaluations tabs only Replay remains.

### 5.4 Steps column

Heading **Steps**. The coding-run story, numbered ("Read", "Model", "Edit (modified)", "Command failed (exit 1)", "Retry #1",
"Tests, attempt 2: fail"); a failed step carries a cross glyph in ink, a retry a caution glyph. For a run with more than one agent
a second heading **Agents** lists them (the agent filter). The column is absent when a run has neither. Below 1100 px it becomes
one strip above the pane: `Steps · 6` (a disclosure).

### 5.5 Timeline rows and the fault line

Columns: Offset, Event (mono event type plus the detail), Duration, Status. No class column. No tinted rows. The markers are plain
text: `error` (ink, 600, with a cross glyph) and `retry` (caution). The first error carries the 2 px fault line and its label on the
line: **First error, step 8**. The hint line in the foot is a sentence ("j and k move, Enter opens, e jumps to the first error,
r replays, Esc closes"), shown from 900 px, never as key caps. Foot count: `12 of 12 events shown` (replay appends `, replay`).
New live events while scrolled away: a text button `3 new events`.

### 5.6 Empty, withheld, error and loading

| State | Headline or sentence | Action |
| --- | --- | --- |
| No events yet | This run has no events yet. | none |
| Filters hide every event | No events match these filters | Clear filters |
| No findings | Nothing wrong found. (analysis current) / No findings yet. (analysis catching up) | none |
| No evidence | the existing sentence ("No artifacts were recorded in this run.") | none |
| No evaluations | the existing Evaluations empty state, without a drawing | the evaluator picker |
| Withheld content | the shared notice style ("i" mark, no bar, no fill): the existing sentences | none |
| Trace expired | **Trace expired**: The trace of this run was removed by the retention policy; the run summary and its numbers remain. Events, findings, the waterfall, replay and evaluations are no longer available. | none (fixed sentence, no date) |
| Trace being removed | **Trace being removed**: The retention policy is removing the trace of this run. Events, findings, the waterfall, replay and evaluations are no longer shown; the run summary and its numbers remain. | none |
| Comparing an expired run (`TRACE_EXPIRED`) | **Trace expired**: The trace of this run was removed by the retention policy; the run summary and its numbers remain. A comparison or an evaluation needs the trace. | none |
| Run not found (404) | Not found: This run no longer exists. It may have been removed by the retention policy, or the link may be wrong. | none (no retry for a 404) |
| Run failed to load (network, 5xx) | Could not load this data: the API's fixed sentence (request id) | Try again |
| Live connection | `Live`, `Connecting`, `Reconnecting`, `Live updates unavailable, refreshing periodically`; with partial data: "Partial data: some recent events may be missing until the connection recovers." | none |
| Summary catching up | Summary is updating; figures below may lag the timeline. / Summary failed to update and needs an operator; the timeline is still accurate. | none |
| Loading | `Loading run…` (skeleton frame), `Loading events…` (polite status), findings and evaluations `Loading findings…`, `Loading evaluations…` | none |

---

## 6. Compare (batch 5b)

Strings: `run.ts` (`compare`).

| Element | Copy |
| --- | --- |
| Back link and title | `Runs` `/` **Compare** (h1 `Compare`) |
| Divergence sentence (h2 size, 18/24 600 ink, never red) | `The runs diverged at step 22: A read app/session.py, B read app/oauth.py.` / `The runs took the same steps.` (`(compared on metadata)` when alignment is metadata-only) |
| Outcome line | `The first difference in outcome is at step 26.` / `The outcome first differs at the same step.` |
| Delta line | `Duration -2.2 s, Cost -$0.0309, Calls -3, Files +1` (right minus left, mono, ink-2) |
| Side cells | `A` and `B`, the run name (link, 600), the status glyph and word, then id, agent and age in small type |
| Toolbar | `Steps 34, 13 differ` then **Jump to divergence**, **Previous difference**, **Next difference** |
| Rule label | `Divergence, step 22` on a 2 px ink rule (not red: a fork is not a failure) |
| Outcome label | `First outcome difference` in ink 600 on the row |
| Empty side | No step on this side |
| Folded steps | Show the 18 earlier steps (the same in both runs) |
| Sections | Summary (disclosure **All measures**), Evaluations, Cost by model, Files |
| Hint | Arrow keys move between steps; n and p jump to the next and previous difference. (small, ink-2) |
| Pick state | **Pick two runs to compare** with a link to the runs list |
| Loading | Comparing runs… |
| Errors | as section 4; an expired side gets the `TRACE_EXPIRED` note above |

---

## 7. Review (batch 5c)

Strings: `apps/web/src/copy/review.ts` (`review`). Review is one page with two tabs; the routes stay `/w/{ws}/approvals` and
`/w/{ws}/security`, and `?approval=` and `?state=` keep working.

### 7.1 Frame

| Element | Copy | Notes |
| --- | --- | --- |
| Title (h1) | Review | |
| Count (right of the title) | `3 waiting` | pending approvals; hidden at zero, absent when the person cannot read approvals |
| Tab list | label "Review sections": **Approvals** and **Security findings** (no counts on the tabs: the number is in the title line and on the Pending chip) | the shared Tabs component; a tab the person cannot read is absent; both absent shows no tab list and the page's own "Not available for your role" |
| Foot of Approvals | An approval authorises one action, once. Policies apply to agents that ask through the SDK guard. | small, ink-2 |

### 7.2 Approvals

| Element | Copy |
| --- | --- |
| State tabs (chip density, label "Approval state") | Pending (with the waiting count beside it, decoration), Decided, All |
| Column heads | Request, Risk, State, Requested, Time left |
| Row | capability (mono, 600, the link that opens the panel), the resource under it (mono, small, ink-2); risk as shield glyph and word (R4 filled and 600, R3 outline in caution text, below that ink-2); state as glyph and word (`‖ Waiting` caution, `✓ Approved` ink-2, `Denied` ink 600 with an outline glyph, `Expired` caution); time left `5 h 59 min`, `Expired` at zero |
| List count | `3 requests loaded (end of list)` / `20 requests loaded` |
| Load more | Load more / Loading… |
| Panel heading | the capability (mono 18/24 600) and a **Close** button |
| Panel fields | Capability, Resource, Risk class, Policy, Approver needed, Expires (with "if it expires, the action is denied/allowed"), Requested, Requested by (run link, agent, project), then Decided, Comment, Used when decided |
| Panel sections | Command (shell only: "Approving covers exactly this command, once."), Arguments |
| Decide | Comment (optional) with `0 / 1000`; **Approve** (the page's one filled button), **Deny** (outline, caution text) |
| Confirm (in place, not a dialog) | "Approve this request? It authorises this one action, once." / "Deny this request? The agent is told it was refused." with **Confirm approve** or **Confirm deny**, **Cancel**; Escape undoes the confirm first |
| After a decision | You approved this request. / You denied this request. (a status line, focus moves to it) |
| Read-only role | Your role can read this request but not decide it. |
| Content withheld | The content of this request could not be shown (it was withheld when the request was made), so it cannot be approved. You can deny it. |

| Empty state | Headline | Body |
| --- | --- | --- |
| Pending, first time | Nothing is waiting for you | When an agent asks a person to approve an action, the request appears here. |
| Decided, none | No decided requests yet | Approved, denied, expired and cancelled requests are listed here. |
| All, none | No approval requests yet | Policies that require approval create a request when an agent asks to act. |

No illustration on any of them (step 1, removal 15).

| Error | Headline | Body | Action |
| --- | --- | --- | --- |
| Forbidden | Not available for your role | Your role in this workspace does not include approvals. | none |
| Offline or 5xx on the list | Could not load this data | the API's fixed sentence (request id) | Try again |
| Rate limited | Too many requests | Wait N seconds and try again. | Try again |
| Request not found | Not found | the API's sentence for APPROVAL_NOT_FOUND | none |
| Decision refused (already decided, expired, conflict, offline) | the existing fixed sentences of `decisionErrorText`, as one status line in the panel | nothing optimistic; the form keeps the comment | Try again by pressing the button again |
| Loading | Loading approvals… (list), Loading the request… (panel) | |

### 7.3 Security findings

| Element | Copy |
| --- | --- |
| Facets | Narrow results (N on); groups Severity, Detector, Project, Run started (the shared facet column of the Runs list) |
| Column heads | Severity, Finding, Run, When (the Project column is dropped: the scope control and the Project facet say it) |
| Severity | shield glyph and word: Critical (filled, 600), High (outline, caution text), Medium, Low, Info (outline, ink-2). Order is by severity then newest run first, so the order is readable without colour |
| Rule line | `Rule: a redaction record names a credential pattern` (small, ink-2, the API's fixed text) |
| List count | `10 findings loaded (end of list)` |

| State | Headline | Body | Action |
| --- | --- | --- | --- |
| Clean | Nothing suspicious found | No security findings in this workspace. These detectors are on: (then the detector list) | none |
| Zero results | No findings match | Try clearing a filter or widening the time range. | Clear filters |
| Forbidden | Not available for your role | Your role in this workspace does not include security findings. | none |
| Error | Could not load this data | the API's sentence (request id) | Try again |
| Loading | Loading security findings… | | |

---

## 8. Settings: the one pattern (batch 5c)

Strings: `apps/web/src/copy/settings.ts` (`settings`). The same words are used on every tab so a person learns them once.

### 8.1 Frame and navigation

| Element | Copy | Notes |
| --- | --- | --- |
| Title line | `Settings` (link to the first tab the role may read) `/` the tab's name as the h1 | the shared title line of 5b; at 375 the back link is its own line `‹ Settings` |
| Section list (nav label "Settings sections") | groups **People**: Members, Sessions; **Access**: API keys, Policies, Capture and detectors; **Limits**: Quotas, Retention, Pricing; **Notify**: Alerts; **Record**: Audit log | a tab the role may not read is absent (the same permissions as before); a group with no tab is absent |
| Section list below 900 px | a button `Section: Quotas` with a chevron opens the grouped list | closes on selection, Escape returns focus to the button |
| Tab heading (h2 does not exist; the h1 is the tab name) | each tab starts with its lede, then sections with h2 headings | |
| Section heading | h2, 18/24 600, followed by one sentence (14/20, ink-2, at most 72 characters wide) | |
| Primary button | one filled button per section at most, at the end of the section it saves | |
| Row actions | text buttons: Remove, Revoke, Edit, Delete; destructive ones in caution text | |
| Saved | `Saved.` plus what changed, as a polite status line under the button; it names what the API stored | the existing per-tab sentences are kept |
| Read-only | one plain sentence at the top of the section instead of the form: `Your role can read this but not change it. Owners and admins can.` (per tab, the existing wording is kept where a test pins it) | no disabled form is shown |
| Forbidden tab | Not available for your role / Your role in this workspace does not include `what`. | `what` per tab: members, API keys, pricing, security settings, project quotas, retention, policies, the audit log |

### 8.2 Destructive actions (two steps in place)

The first press turns the button row into a sentence and two buttons. The second press acts. Escape and **Cancel** go back.

| Action | First step | Second step | Cancel |
| --- | --- | --- | --- |
| Remove a member | Remove | Confirm remove | Cancel |
| Revoke an API key | Revoke | Confirm revoke | Cancel |
| Revoke an invitation | Revoke | Confirm revoke | Cancel |
| End one session | End session | (one step: it signs a device out and the list says so) | |
| Sign out everywhere else | Sign out everywhere else | Confirm sign out everywhere else | Cancel |
| Delete an alert rule | Delete | Delete NAME (the rule's name is the confirmation) | Keep |
| Delete or rotate a destination | as before | as before | as before |
| Shorten a retention period | Save periods | Save and shorten | Cancel |

### 8.3 Field errors, helper text, conflict, offline

- A field error is one sentence under the field, ink, with a leading `!` mark in a 600 weight; never red (red is run failure only). The field has `aria-invalid` and `aria-describedby`.
- A write that fails shows one status line under the section: the API's fixed sentence, then `(request ID)` in small type. A 403 reads `Your role does not allow this.` first.
- A conflict (policy version, concurrent edit) keeps the draft and says what to do: `Someone published a newer version (version 4). Reload to continue from it; your draft in the editor will be replaced.` with **Reload the latest version**.
- Rate limited: `Too many requests. Wait N seconds and try again.` Offline: `The request failed.` with **Try again**; the form keeps its values.
- Helper text is 13/20 ink-2 directly under the field, never a placeholder.

### 8.4 Per tab

| Tab | Title (h1) | Lede | Sections (h2) | Primary | Empty / read-only |
| --- | --- | --- | --- | --- | --- |
| Members | Members | Who can open this workspace, and what they may do. | Members; Invitations | Create invitation | No members / No open invitations |
| Sessions | Sessions | The browsers and devices where you are signed in. Ending a session signs that device out at once. Each change is written to the audit log of your workspaces. | Your sessions | none | No sessions: You have no working session. Reload the page to sign in again. |
| API keys | API keys | Keys let SDKs and tools send events and read runs. A token is shown once, when the key is created. | Keys; Create a key | Create key | No API keys: Create one to connect an agent. |
| Policies | Policies | Rules for what an agent may do. The first rule that matches decides; anything that matches no rule is allowed. | Policy text; Dry run; Publish; Versions | Publish | No policy yet: Start from the example and edit it, or write your own. Read-only: You can read policies but not change them. Owners and admins edit and publish. |
| Capture and detectors | Capture and detectors | What the server stores, which detectors report and which hosts an agent may contact, for one project. Every change is written to the audit log. | Capture mode; Detectors; Allowed hosts | Save changes | No projects yet: Settings appear once an agent reports a project. |
| Quotas | Quotas | What one project may store. At a limit, new items are refused with a clear code; what the project already has keeps working. Changes to a limit are written to the audit log. | Usage and limits | Save limits | read-only: Owners and admins can change these limits. |
| Retention | Retention | How long this workspace keeps its data. Older data is removed by a background job, a run at a time, and every change to these periods is written to the audit log. | Mode; Periods; Last passes | Save periods | read-only: Owners and admins can change these periods. |
| Pricing | Pricing | Prices per million tokens used to cost LLM calls. An override replaces the built-in price for matching models from its start time; past runs change only after a rebuild. | Prices; Add an override; Apply to past runs | Add override | No prices |
| Alerts | Alerts | Tell a person or a channel when a run fails, a security finding appears, an approval needs attention or spend goes over a limit. | tabs Rules, Destinations, History | Add a rule / Add a destination | see section 9 |
| Audit log | Audit log | Administrative actions and refused attempts, newest first. | Entries | none (Load more) | Nothing recorded yet |

### 8.5 Quotas and usage words

`12 of 500 (2 %)` mono; state word with glyph: `✓ Within limit` ink-2, `◐ Near limit` caution (from 80 %), `● Limit reached` ink 600; the bar is 4 px, ink-2, caution from 80 %, ink at the limit; never red. At the limit one sentence under the row: the quota's refusal sentence plus `Starts again at 2026-10-17 00:00 UTC.` for daily quotas.

### 8.6 Loading and transitions

| Moment | Copy |
| --- | --- |
| Tab loading | `Loading members…` and so on: one polite status line in place of the section; no skeleton |
| Saving | the button reads `Saving…` and is disabled; the rest stays |
| Saved | the status line under the button; focus stays where it was |
| Navigating away with unsaved text (Policies, signing secret) | the browser's own leave prompt with the existing sentence |

---

## 9. Alerts, under Settings (batch 5c)

Strings: `apps/web/src/copy/settings.ts` (`settings.alerts`). Route `/w/{ws}/settings/alerts?tab=rules|destinations|history`; `/w/{ws}/alerts` redirects there with the tab kept.

| Element | Copy |
| --- | --- |
| Tab list (label "Alert sections") | Rules, Destinations, History (the shared Tabs component, URL `?tab=`) |
| Foot | Alerts are notifications that are sent as soon as possible and may arrive more than once. They carry names, ids and counts, never what an agent read, wrote or ran. |
| Rules, columns | Name, When, Only if, Sends to, On, Last fired; row actions **Edit** and **Delete** as text buttons |
| Rules, first time | Add a destination, then a rule: A destination is where alerts go: a webhook, a Slack channel or an email address. A rule says what to watch for. then **Add a destination** |
| Rules, destination exists | Now add a rule: A rule says what to watch for and which destinations to tell. then **Start with "a run fails"** and **Add a rule** |
| Rule delete | Delete, then `Delete this rule?` **Delete NAME** / **Keep** |
| Rule saved | Saved NAME. then the 7-day preview line (counts only), **Dismiss** |
| Destinations, columns | Name, Target, Health, Rules; **Send a test**, **Pause**/**Resume**, **Rotate secret**, **Delete** |
| Destinations, role | Destinations are for owners and admins: A destination holds a private address or a signing secret, so only owners and admins can see or change them. You can still read the rules and the history of what was sent. |
| Send a test | `Sending a test…`, then `Test sent.` or `Test failed: CODE in words`; nothing is stored |
| Signing secret panel (unchanged words) | Signing secret for NAME. It is shown once and is not kept in this browser. ... **Copy secret**, "I have stored the secret", **Done** (disabled until ticked); leaving the page asks first |
| History | filters (Destination, Outcome, Rule), table (When, Rule, Destination, Outcome, Attempts), Load more; empty `Nothing has been sent yet` / `No alerts match these filters` |
| Field errors | the existing fixed sentences of `problemFrom` (for example "Only port 443 is allowed" is the API's reason), shown as the shared field error |
