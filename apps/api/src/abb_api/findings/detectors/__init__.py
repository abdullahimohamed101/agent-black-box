"""The detector set, its version and `run_all`.

Bump `DETECTORS_VERSION` whenever any detector's rules or the evidence contract change: stored
findings with an older version read as `outdated` until a rebuild (ADR-080).
"""

import json

from abb_api.findings.detectors.cascade import ToolFailureCascade
from abb_api.findings.detectors.chain import FailureChain
from abb_api.findings.detectors.context import ContextGrowth
from abb_api.findings.detectors.cost_spike import CostSpike
from abb_api.findings.detectors.identical import IdenticalRequest
from abb_api.findings.detectors.reported import ReportedLoop
from abb_api.findings.detectors.retries import RetryStorm
from abb_api.findings.detectors.sequence import RepeatedSequence
from abb_api.findings.model import (
    MAX_PER_DETECTOR,
    MAX_PER_RUN,
    MAX_RUN_EVIDENCE_BYTES,
    Detector,
    DetectorContext,
    DetectorError,
    Finding,
)

DETECTORS_VERSION = 1

DETECTORS: tuple[Detector, ...] = (
    RepeatedSequence(),
    IdenticalRequest(),
    RetryStorm(),
    ContextGrowth(),
    CostSpike(),
    ToolFailureCascade(),
    FailureChain(),
    ReportedLoop(),
)


def _evidence_bytes(finding: Finding) -> int:
    return len(json.dumps(finding.evidence, separators=(",", ":")).encode("utf-8"))


def run_all(ctx: DetectorContext, detectors: tuple[Detector, ...] = DETECTORS) -> list[Finding]:
    """Every finding of the run, de-duplicated, bounded and in a stable order.

    A detector exception is re-raised as `DetectorError` with a fixed message, `from None`: a bug
    must surface (the job fails, retries and dead-letters), but never carry an attribute value into
    `outbox_jobs.last_error` or a traceback.
    """
    by_fingerprint: dict[str, Finding] = {}
    for detector in detectors:
        try:
            produced = detector.detect(ctx)
        except Exception:
            raise DetectorError(detector.name, ctx.facts.event_count) from None
        for finding in produced:
            kept = by_fingerprint.get(finding.fingerprint)
            if kept is None or finding.severity.rank > kept.severity.rank:
                by_fingerprint[finding.fingerprint] = finding

    end = len(ctx.events)

    def order(f: Finding) -> tuple[int, int, str, str, str]:
        start = ctx.position.get(f.start_event_id, end) if f.start_event_id else end
        return (-f.severity.rank, start, f.detector, f.finding_type, f.fingerprint)

    ordered = sorted(by_fingerprint.values(), key=order)
    per_detector: dict[str, int] = {}
    bounded: list[Finding] = []
    for finding in ordered:  # lowest severity is dropped first: the list is severity-descending
        count = per_detector.get(finding.detector, 0)
        if count >= MAX_PER_DETECTOR or len(bounded) >= MAX_PER_RUN:
            continue
        per_detector[finding.detector] = count + 1
        bounded.append(finding)
    budget = MAX_RUN_EVIDENCE_BYTES
    within: list[Finding] = []
    for finding in bounded:
        budget -= _evidence_bytes(finding)
        if budget < 0:
            break
        within.append(finding)
    return within
