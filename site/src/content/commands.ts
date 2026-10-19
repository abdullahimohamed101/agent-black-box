import { REPO_URL } from "../config";
import { python, shell, type ShellCommand } from "../lib/code";

/**
 * The quickstart, in one place: the Quickstart section and the closing call to action both render these. They are the commands
 * of docs/quickstart.md (steps 1 and 2) followed by `make demo`; `scripts/check-site.sh commands` fails when that document exists
 * and no longer contains each of them.
 */
export const COMMANDS: ShellCommand[] = [
  { cmd: `git clone ${REPO_URL}.git` },
  { cmd: "cd agent-black-box" },
  { cmd: "cp .env.example .env" },
  { cmd: "make up", comment: "PostgreSQL, the API, a worker and the web app, in Docker" },
  { cmd: "make demo", comment: "records the scripted example agents" },
];

export const QUICKSTART_LINES = shell(COMMANDS);
export const ONE_LINE = COMMANDS.map((c) => c.cmd).join(" && ");

/** The README's SDK snippet, verbatim. */
export const SDK_SNIPPET = python(`from blackbox import BlackBox

bb = BlackBox(api_key=..., project="coding-agent")
with bb.run("Fix OAuth timeout") as run:
    with run.span("run-tests", kind="shell"):
        agent.execute()`);

export const PREREQUISITES = "Docker with Compose · uv · Git · curl · python3";
