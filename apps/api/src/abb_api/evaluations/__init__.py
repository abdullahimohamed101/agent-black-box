"""Evaluations (spec §85, ADR-090): derived, versioned results of built-in evaluators.

An evaluation is the result of applying one evaluator definition to a run's events. It never
changes the run, its spans, events or cost; a failing evaluator is a `failed` result.
"""
