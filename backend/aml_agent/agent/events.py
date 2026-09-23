"""The narrow safe-event contract used by the orchestrator."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class AgentEventStore(Protocol):
    def record(
        self,
        run_id: str,
        kind: str,
        summary: str,
        *,
        tool_name: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None: ...
