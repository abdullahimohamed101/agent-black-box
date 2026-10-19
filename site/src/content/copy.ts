// Page copy that is shared by more than one section or by the metadata. Wording is the positioning brief's (section 7);
// anything that waits for publication is built from RELEASE_CLAIMS in config.ts, never typed twice.
import { RELEASE_CLAIMS } from "../config";

export const NAME = "Agent BlackBox";
export const H1 = "See what your agent actually did.";

/** Hero sub. "Self-hosted" is a product claim and waits for G2 (claims table: unverified until the deployment commands run). */
export const SUB = RELEASE_CLAIMS
  ? "A self-hosted flight recorder for AI agents. Every model call, tool call, command and retry, as typed events, and the step where it broke."
  : "A flight recorder for AI agents. Every model call, tool call, command and retry, as typed events, and the step where it broke.";

export const FACTS = ["Apache-2.0", "Python 3.12", "Docker", "PostgreSQL only"];

/** Verbatim from the positioning brief (section 7, "Honest status line"). */
export const STATUS_LINE =
  "Early release. Single-node Docker Compose. No hosting guide yet (TLS, domain, real OIDC are documented as not covered). Real-model mode exists in two examples and has not been run by the project.";
