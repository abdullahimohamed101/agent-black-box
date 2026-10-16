# Design pass 2, step 5: microcopy, errors, empty states and loading

Status: batch 5a written 2026-10-16 (shell, navigation, sign-in and no-access pages, the Runs home). Later batches append their
sections below (5b Run and Compare, 5c Review, Alerts, Analytics, Experiments, 5d Settings). Source prompt: Prompt 1 sections 4 to 7
(`docs/design/design-pass-2-prompts.md`). Voice from `01-direction.md` (voice cues): sentences, not labels; one verb per button;
numbers carry units; no upper case shouting; no marketing tone; never echo captured content (names of runs, commands, payload
text, search text). A string that includes a run, agent or workspace name renders it as a text node and appears only where the
person already sees that name.

The strings live in typed modules, one per area, in `apps/web/src/copy/` (`shell.ts`, `auth.ts`, `runs.ts`, `states.ts`). A
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
| Person panel | Signed in as, the email, "Role: Owner", links Sessions and Alerts, button Sign out | Alerts sits here until the Settings batch moves it under Settings |
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
| Search field | label "Search runs"; placeholder `Search runs, for example status:failed tool:github "npm test"`; a **?** button named "Search help" |
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
| Zero results for a filter | No runs match these filters | Try clearing a filter. | Clear filters |
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
