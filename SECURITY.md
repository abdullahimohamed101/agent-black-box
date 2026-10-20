# Security policy

Agent BlackBox stores prompts, tool arguments, shell output and file names from the agents it
observes, so security reports are taken seriously. The technical security model (API keys, tenancy,
redaction, retention) is documented in `docs/SECURITY.md`.

## Supported versions

The project is an early release without tagged versions. Only the latest commit on `main` is
supported; fixes are not backported.

## Reporting a vulnerability

Please report privately and do not open a public issue or pull request for it.

- Use GitHub's private vulnerability reporting: the "Report a vulnerability" button under the
  repository's Security tab (an advisory form visible only to the maintainers).
- Or email itsabdullahi101@gmail.com.

Include what you found, the affected component and version or commit, steps to reproduce, and the
impact you expect. Do not include real customer data or secrets in a report.

## What counts

Examples we want to hear about: reading or writing another workspace's data, authentication or
API-key bypass, secrets or payloads surviving redaction, stored events or artifacts altered after
acceptance, an SDK failure that can crash or hang a host agent, and injection through captured
telemetry into the web app.

Out of scope: findings that need an attacker who already controls the host or the database, denial
of service by volume against a self-hosted instance without rate limiting you chose to disable,
and issues in third-party software that this project only depends on (report those upstream).

## What to expect

This is a small project, so these are intentions, not guarantees: an acknowledgement within a few
days, an assessment, and a fix or a stated decision. We will credit you in the advisory if you want
that. Please give us reasonable time to fix an issue before you disclose it publicly.
