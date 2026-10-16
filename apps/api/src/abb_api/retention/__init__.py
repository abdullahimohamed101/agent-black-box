"""Data lifecycle (Phase 19b, ADR-159): retention policy, preview, and the job that applies it.

Only `runner` and `tick` use the retention database role; the API process imports neither.
"""
