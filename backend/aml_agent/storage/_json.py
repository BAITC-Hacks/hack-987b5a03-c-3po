"""Strict JSON helpers used by the persistence layer.

SQLite stores compact application payloads as JSON text.  Keeping the codec in
one place prevents accidental persistence of non-standard values such as NaN,
which different JSON consumers handle inconsistently.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import UUID


def _default(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("naive datetimes cannot be encoded as persisted JSON")
        normalized = value.astimezone(UTC)
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def dumps(value: Any) -> str:
    """Return canonical, standards-compliant JSON text."""

    return json.dumps(
        value,
        allow_nan=False,
        default=_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def loads(value: str) -> Any:
    """Decode persisted JSON text."""

    return json.loads(value)
