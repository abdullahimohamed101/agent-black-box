"""Counters that are driven by the API's own refusals (Phase 19c step 2).

A few controls refuse by raising an `AppError` with a fixed code: a quota, a full search gate, an
invitation-only sign-in. They are counted once, where the error handler turns the error into a
response, so every present and future raise site is covered and none needs a registry handle. The
label values come from the error's closed `details` vocabulary and the catalogue folds anything
else into ``other``; no request text is ever used.
"""

from collections.abc import Mapping
from typing import Any

from abb_api.obs.catalogue import Catalogue


def count_app_error(catalogue: Catalogue, code: str, details: Mapping[str, Any]) -> None:
    if code == "QUOTA_EXCEEDED":
        catalogue.quota_refusals_total.inc(str(details.get("quota", "")))
    elif code == "SEARCH_BUSY":
        catalogue.search_busy_total.inc(str(details.get("reason", "")))
    elif code == "NO_ACCESS":
        catalogue.invitation_only_refusals_total.inc()
