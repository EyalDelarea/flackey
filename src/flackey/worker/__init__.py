"""The download worker: takes each queued request through identify, fetch, verify and file.

`Worker` lives in `core` and is assembled from one mixin per stage -- `pipeline` (identify and choose),
`acoustic` (the fingerprint reference), `lossless_attempt` (the Soulseek path), `filing` (verify, file,
upgrade) and `recovery` (what a pass that filed nothing does next). The retry numbers and outcome sets
are in `policy`; stand-in candidates and the small value types are in `records`."""
from .core import Worker
from .policy import (
    CANCELLABLE,
    LOGIN_REQUIRED,
    LONG_RETRY_EVERY_S,
    LONG_RETRY_OUTCOMES,
    LONG_RETRY_TIMES,
    MAX_ATTEMPTS,
    RETRY_BACKOFF_S,
    RETRY_LOSSLESS_OUTCOMES,
    SEARCH_AGAIN_AFTER,
    SECOND_PICK_AFTER,
    SECOND_SEARCH_AFTER,
    STAGE_OF_STATE,
)
from .records import (
    CATALOG_SOURCE,
    QUERY_SOURCE,
    Acoustic,
    CatalogLike,
    LosslessHit,
    catalog_candidate,
    format_line,
    query_candidate,
)

__all__ = [
    "CANCELLABLE", "CATALOG_SOURCE", "LOGIN_REQUIRED", "LONG_RETRY_EVERY_S", "LONG_RETRY_OUTCOMES",
    "LONG_RETRY_TIMES", "MAX_ATTEMPTS", "QUERY_SOURCE", "RETRY_BACKOFF_S", "RETRY_LOSSLESS_OUTCOMES",
    "SEARCH_AGAIN_AFTER", "SECOND_PICK_AFTER", "SECOND_SEARCH_AFTER", "STAGE_OF_STATE", "Acoustic",
    "CatalogLike", "LosslessHit", "Worker", "catalog_candidate", "format_line", "query_candidate",
]
