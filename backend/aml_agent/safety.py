"""Fixed MVP safety policy shared by tool and persistence boundaries."""

from __future__ import annotations

from typing import Final

SAFE_REVIEW_CASE_TITLES: Final[tuple[str, ...]] = (
    "Priority structural indicators for analyst review",
    "Network indicators for analyst review",
    "Структурные индикаторы для проверки аналитиком",
)
_SAFE_REVIEW_CASE_TITLE_SET: Final[frozenset[str]] = frozenset(SAFE_REVIEW_CASE_TITLES)


def is_safe_review_case_title(title: object) -> bool:
    """Return whether a title is one of the reviewed, immutable MVP labels."""

    return isinstance(title, str) and title in _SAFE_REVIEW_CASE_TITLE_SET


__all__ = ["SAFE_REVIEW_CASE_TITLES", "is_safe_review_case_title"]
