"""repeated-sequence: the same block of actions repeated back to back (a probable loop).

Tokens are closers and point events (`tokens.token_for`) per emitter. For each block length n the
detector makes one pass that finds maximal stretches in which token[j] == token[j + n]; a stretch
of L such positions is a periodic region of L + n tokens holding (L + n) // n repetitions. Shorter
blocks win ties on coverage, so a loop of period 2 is never reported as period 4, and blocks that
are themselves periodic are skipped (a block `[A, A]` is the one-token loop `A`, which has its own,
higher threshold).
"""

from abb_api.findings.model import (
    MAX_PATTERN_TOKENS,
    MAX_PER_DETECTOR,
    Detector,
    DetectorContext,
    Finding,
    Kind,
    Severity,
)
from abb_api.findings.tokens import Token, millis, tokens_by_agent

THRESHOLD_NAMES = (
    "loop_min_repetitions",
    "loop_min_repetitions_single",
    "loop_min_tokens",
    "loop_max_pattern_length",
)


def _primitive(block: list[int]) -> bool:
    n = len(block)
    for d in range(1, n):
        if n % d == 0 and all(block[i] == block[i + d] for i in range(n - d)):
            return False
    return True


class RepeatedSequence(Detector):
    name = "repeated-sequence"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        found: list[Finding] = []
        for agent, tokens in tokens_by_agent(ctx.events).items():
            found.extend(self._agent(ctx, agent, tokens))
        return found

    def _agent(self, ctx: DetectorContext, agent: str, tokens: list[Token]) -> list[Finding]:
        cfg = ctx.config
        m = len(tokens)
        if m < cfg.loop_min_tokens:
            return []
        interned: dict[str, int] = {}
        ids = [interned.setdefault(t.text, len(interned)) for t in tokens]

        candidates: list[tuple[int, int, int, int]] = []  # (-coverage, n, start, repetitions)
        for n in range(1, min(cfg.loop_max_pattern_length, m // 2) + 1):
            need = cfg.loop_min_repetitions_single if n == 1 else cfg.loop_min_repetitions
            length = start = 0
            for j in range(m - n + 1):
                if j < m - n and ids[j] == ids[j + n]:
                    if length == 0:
                        start = j
                    length += 1
                    continue
                if length:
                    reps = (length + n) // n
                    if (
                        reps >= need
                        and reps * n >= cfg.loop_min_tokens
                        and _primitive(ids[start : start + n])
                    ):
                        candidates.append((-reps * n, n, start, reps))
                    length = 0

        covered = bytearray(m)
        accepted: list[tuple[int, int, int, int]] = []
        for neg_cover, n, start, reps in sorted(candidates):
            end = start - neg_cover
            if 1 in covered[start:end]:
                continue
            covered[start:end] = b"\x01" * (end - start)
            accepted.append((start, end, n, reps))
            if len(accepted) >= MAX_PER_DETECTOR:
                break
        return [self._finding(ctx, agent, tokens, *a) for a in sorted(accepted)]

    def _finding(
        self,
        ctx: DetectorContext,
        agent: str,
        tokens: list[Token],
        start: int,
        end: int,
        n: int,
        reps: int,
    ) -> Finding:
        pattern = [tokens[start + i].text for i in range(n)]
        first, last = tokens[start].event, tokens[end - 1].event
        succeeded = ctx.facts.status.value == "SUCCESS"
        # ADR-081: a probable loop on a run that succeeded is `info` however long (a coding agent
        # that read-edit-tests twelve files is not in trouble); only a run that did not succeed, or
        # is still active, can read `warning`/`critical`, and it may change when the run ends.
        if succeeded:
            severity = Severity.INFO
        elif reps >= 10:
            severity = Severity.CRITICAL
        else:
            severity = Severity.WARNING
        return self.make(
            ctx,
            finding_type="possible_loop",
            kind=Kind.PROBABLE,
            severity=severity,
            confidence=min(0.95, 0.5 + 0.05 * reps),
            start_event_id=first.event_id,
            end_event_id=last.event_id,
            key=agent + "\x1e" + "\x1f".join(pattern),
            evidence={
                "agent_id": agent,
                "pattern": pattern[:MAX_PATTERN_TOKENS],
                "repetitions": reps,
                "event_ids": [tokens[start + r * n].event.event_id for r in range(reps)],
                "start_event_id": first.event_id,
                "end_event_id": last.event_id,
                "duration_ms": millis(last, first),
                "thresholds": ctx.config.thresholds(*THRESHOLD_NAMES),
            },
        )
