"""Reliability intelligence: pure detectors over a run's events and the finding contract (§80-81).

Findings are derived records (INV-2): everything here is a function of the run's events (plus the
run's cost and a cost baseline for the one detector that needs them). Evidence is metadata only,
by construction (ADR-080): see `model.EVIDENCE_KEYS`.
"""
