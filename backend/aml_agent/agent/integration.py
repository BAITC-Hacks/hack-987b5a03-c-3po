"""Adapters from the agent contracts to Phase 2 storage and tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aml_agent.storage import Database, EventKind, RunState, VerificationStatus
from aml_agent.tool_runtime import ToolRuntime
from aml_agent.tools import get_allowed_tool_definitions

from .catalog import TOOL_DEFINITIONS
from .models import AgentDecision, RunContext

_COUNT_FIELDS = {
    "inspect_dataset": ("n_nodes", "n_edges", "n_transactions", "n_seed"),
    "cluster_network": ("n_clusters",),
    "assign_roles": ("assigned_count",),
    "rank_targets": ("ranked_count",),
    "create_review_case": ("target_count",),
}
_WORKFLOW = (
    "inspect_dataset",
    "build_graph",
    "compute_graph_features",
    "cluster_network",
    "assign_roles",
    "rank_targets",
    "create_review_case",
    "export_results",
    "verify_run",
)
_EVENT_KINDS = frozenset(kind.value for kind in EventKind)
_EVENT_PAYLOAD_KEYS = frozenset(
    {"attempt", "error_code", "passed", "remaining_tool_budget", "state"}
)


class DatabaseRunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_compact_context(self, run_id: str) -> RunContext:
        run = self.database.require_run(run_id)
        records = {name: self.database.get_recorded_tool_result(run_id, name) for name in _WORKFLOW}
        counts: dict[str, int] = {}
        completed_tools: list[str] = []
        for name, record in records.items():
            if record is None or record.payload.get("ok") is not True:
                continue
            completed_tools.append(name)
            data = record.payload.get("data", {})
            if isinstance(data, Mapping):
                counts.update(
                    {
                        key: value
                        for key in _COUNT_FIELDS.get(name, ())
                        if type(value := data.get(key)) is int and value >= 0
                    }
                )
        ranked = records["rank_targets"]
        target_gids = ()
        if ranked is not None and ranked.payload.get("ok") is True:
            target_gids = tuple(ranked.payload.get("data", {}).get("target_gids", ()))
        review_case = self.database.get_review_case_for_run(run_id)
        warnings = tuple(
            event.summary[:240]
            for event in self.database.list_events(run_id)
            if event.kind is EventKind.WARNING
        )[-10:]
        return RunContext(
            run_id=run.run_id,
            state=run.status,
            mode=run.mode.value,
            completed_tools=tuple(completed_tools),
            counts=counts,
            warnings=warnings,
            target_gids=target_gids,
            case_id=None if review_case is None else review_case.case_id,
            verification_status=run.verification_status.value,
            analyst_confirmation_required=run.metadata.get("analyst_confirmation_required") is True,
        )

    def complete_run(self, run_id: str, decision: AgentDecision) -> None:
        run = self.database.require_run(run_id)
        if run.verification_status is not VerificationStatus.PASSED:
            raise ValueError("Verification has not passed")
        if run.status is RunState.COMPLETED:
            return
        self.database.complete_run(run_id)

    def fail_run(self, run_id: str, error_code: str) -> None:
        run = self.database.require_run(run_id)
        if run.status in (RunState.FAILED, RunState.VERIFICATION_FAILED):
            return
        if run.status is RunState.COMPLETED:
            raise ValueError("Completed runs are immutable")
        target = (
            RunState.VERIFICATION_FAILED if error_code == "VERIFICATION_FAILED" else RunState.FAILED
        )
        self.database.transition_run(run_id, target)


class DatabaseEventStore:
    """Agent events share the existing audit table; the tool runtime owns tool events."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def record(
        self,
        run_id: str,
        kind: str,
        summary: str,
        *,
        tool_name: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        if kind not in _EVENT_KINDS or (
            tool_name is not None and tool_name not in TOOL_DEFINITIONS
        ):
            raise ValueError("Invalid agent event metadata")
        if not isinstance(summary, str) or not summary or len(summary) > 240 or "\n" in summary:
            raise ValueError("Invalid agent event summary")
        safe_payload = dict(payload or {})
        if set(safe_payload) - _EVENT_PAYLOAD_KEYS or any(
            value is not None and type(value) not in (bool, int, str)
            for value in safe_payload.values()
        ):
            raise ValueError("Unsafe agent event payload")
        if any(isinstance(value, str) and len(value) > 80 for value in safe_payload.values()):
            raise ValueError("Agent event payload is too long")
        # ToolRuntime already records these; completed runs cannot be mutated.
        if kind in {"tool_started", "tool_completed", "verification", "completed"}:
            return
        if self.database.require_run(run_id).status is RunState.COMPLETED:
            return
        self.database.append_event(run_id, kind, summary, tool_name=tool_name, payload=safe_payload)


class RuntimeToolRegistry:
    def __init__(self, runtime: ToolRuntime) -> None:
        self.runtime = runtime

    def allowed_definitions(self, state: RunState) -> list[dict[str, Any]]:
        return get_allowed_tool_definitions(state)

    async def execute(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self.runtime.execute(name, dict(arguments))
