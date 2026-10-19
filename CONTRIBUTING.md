# Contributing to Agent BlackBox

Thanks for helping. Agent BlackBox is a flight recorder for AI agents, and its one promise is that
what it shows is what the agent actually did. Changes are judged against that promise first.

## License

The repository is Apache-2.0 (see `LICENSE`). By submitting a contribution you license it under
Apache-2.0 as described in section 5 of that license. There is no CLA and no sign-off requirement.

## Set up

```bash
cp .env.example .env     # local development values only; never commit .env
make setup               # Python and JS dependencies, Postgres in Docker, migrations
make dev                 # API and web natively
```

Requirements and macOS notes are in `docs/development/setup.md`.

## Checks before you open a pull request

```bash
scripts/quality.sh quick   # lint, types and tests for every package
scripts/quality.sh full    # quick, plus the migration up/down/up cycle and the builds
```

Both need `ABB_DATABASE_URL` (and `ABB_TEST_DATABASE_URL` for `full`) from your `.env`. UI changes
should also be exercised in a browser, not only built. Do not delete, skip or weaken a failing test to
get green; fix the cause.

## How changes are organised

Work is planned in phases. For a larger change:

1. Write or update a plan in `docs/plans/active/` (one per phase, with acceptance criteria that name a
   command and its expected output).
2. Work on a branch named `feature/<short-name>`; never commit to `main`. Use conventional commit
   messages (`feat(api): ...`, `fix(web): ...`, `docs: ...`), one coherent commit per unit of work.
3. If you deviate from the specification in `docs/architecture/agent-black-box-spec.md`, record the
   decision as an ADR in `docs/decisions/` (indexed in `docs/DECISIONS.md`) before changing the code.
4. A change is done when each acceptance criterion is shown with a command and its output.

Small fixes (typos, a bug with a test) do not need a plan; open the pull request directly.

## Rules that are not negotiable

These are the architectural invariants from `AGENTS.md`; a pull request that breaks one needs an ADR first.

- Accepted events are immutable; corrections are new events or derived records.
- Derived state (summaries, costs, findings) is rebuildable from events.
- Every tenant-data operation carries workspace context.
- The SDK must never crash the host agent; its buffers and retries are bounded.
- Capturing sensitive payloads is opt-in, and redaction happens before export.
- Agent concepts are typed events, not log strings.
- Storage sits behind contracts; no ad-hoc SQL against events from routers.
- External conventions (OpenTelemetry) are mapped at the boundary.

Also: treat captured telemetry as hostile input, give every external call a timeout, bound every
retry loop and queue, and never log payload bodies or secrets.

## Dependencies

The SDK has a near-zero runtime dependency budget (`packages/sdk-python` is standard library only).
Elsewhere, a new dependency needs a reason: what it replaces, its license (must be compatible with
Apache-2.0), its maintenance state and its size.

## Reporting problems

- Bugs and ideas: open an issue using the templates.
- Security issues: do not open a public issue; follow `SECURITY.md`.
- Conduct concerns: see `CODE_OF_CONDUCT.md`.
