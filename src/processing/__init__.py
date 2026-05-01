"""Processing — dedup, classification, routing / 处理层"""
from .dedup import (
    BODY_TRUNCATE_CHARS,
    content_hash,
    filter_new,
    is_duplicate,
)
from .triage import (
    DEFAULT_BODY_EXCERPT_CHARS,
    VALID_TRACKS,
    TriageResult,
    TriageRunSummary,
    apply_triage,
    run_pending_triage,
    triage_one,
)

__all__ = [
    # dedup
    "BODY_TRUNCATE_CHARS",
    "content_hash",
    "filter_new",
    "is_duplicate",
    # triage
    "DEFAULT_BODY_EXCERPT_CHARS",
    "VALID_TRACKS",
    "TriageResult",
    "TriageRunSummary",
    "apply_triage",
    "run_pending_triage",
    "triage_one",
]
