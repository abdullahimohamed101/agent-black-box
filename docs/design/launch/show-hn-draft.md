# Show HN draft (gate G2: post only after the repository is public and the site is live)

**Title:** `Show HN: Agent BlackBox – self-hosted flight recorder for AI agents (Python, Postgres)`

Link the repository, not the site. The claims below follow `docs/plans/active/phase-20-release-polish.md` (Claims table);
re-check each against that table on the day.

**First comment** (post it yourself right after submitting):

> I built this because when an agent run goes wrong the useful question is "which step broke, and what had it already done?",
> and logs make that hard to answer. Agent BlackBox records every model call, tool call, shell command, file edit and retry
> as a typed event, and shows the run as a timeline with one red line at the first error and a one-sentence headline.
>
> You can try it in a few minutes without any API key: `make up`, then `make demo` records twelve scripted example runs
> (a failed support run, a five-agent job, a pending approval) into a local stack.
>
> Things it does not do, so you do not have to find them yourself:
>
> - Policies and approvals are cooperative: they apply to agents that ask through the SDK guard. Code that bypasses the guard is
>   recorded, not stopped.
> - There is no load test, so I will not quote capacity or overhead numbers.
> - The adapters for LangGraph, OpenAI, Anthropic and MCP are tested offline against the real libraries, not against live
>   providers; the real-model mode of the examples exists but has never been run by the project.
> - There is no hosting guide yet; it runs on one machine with Docker Compose and PostgreSQL.
>
> It is not trying to replace tracing tools you already use; it exports OpenTelemetry but does not ingest it.
> I would most like feedback on whether the run page answers "where did it break" fast enough, and on what is missing from the
> detectors (loops, retry storms, failure chains, six security rules).

**Rules for the day:** reply to every comment in the first hour; no asks for upvotes; no claims beyond the list above.
