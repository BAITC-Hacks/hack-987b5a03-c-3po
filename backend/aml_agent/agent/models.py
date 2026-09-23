"""Small, provider-independent records crossing the agent boundary."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from aml_agent.storage import RunState


def is_valid_gid(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.isascii()
        and value.isdigit()
        and 1 <= len(value) <= 19
        and int(value) <= 2**63 - 1
    )


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: str
    state: RunState
    mode: Literal["demo", "live"]
    completed_tools: tuple[str, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    target_gids: tuple[str, ...] = ()
    case_id: str | None = None
    verification_status: Literal["pending", "passed", "failed"] = "pending"
    analyst_confirmation_required: bool = False
    remaining_tool_budget: int = 12

    def compact(self) -> dict[str, Any]:
        """Return bounded operational context, with no data rows or local paths."""
        if any(not is_valid_gid(gid) for gid in self.target_gids):
            raise ValueError("Invalid ranked GID")
        allowed_counts = {
            "n_nodes",
            "n_edges",
            "n_transactions",
            "n_seed",
            "n_clusters",
            "assigned_count",
            "ranked_count",
            "target_count",
            "warning_count",
        }
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "mode": self.mode,
            "completed_tools": list(self.completed_tools[-12:]),
            "counts": {
                key: value
                for key, value in self.counts.items()
                if key in allowed_counts and type(value) is int and value >= 0
            },
            "warnings": [item[:240] for item in self.warnings[:10] if isinstance(item, str)],
            "target_gids": list(self.target_gids[:100]) if self.state == RunState.RANKED else [],
            "analyst_confirmation_required": self.analyst_confirmation_required,
            "remaining_tool_budget": self.remaining_tool_budget,
            "case_id": self.case_id,
            "verification_status": self.verification_status,
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str | Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    response_id: str | None
    calls: tuple[ToolCall, ...]


ERROR_CODES = frozenset(
    {
        "INVALID_STATE",
        "INVALID_ARGUMENT",
        "DATASET_NOT_FOUND",
        "DATASET_SCHEMA_INVALID",
        "DATASET_INCONSISTENT",
        "ANALYTICS_FAILED",
        "CASE_WRITE_FAILED",
        "EXPORT_FAILED",
        "VERIFICATION_FAILED",
        "INTERNAL_ERROR",
    }
)


@dataclass(frozen=True, slots=True)
class ToolError:
    code: str
    retriable: bool = False


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    tool: str
    run_id: str
    data: Mapping[str, Any] = field(default_factory=dict)
    error: ToolError | None = None

    @classmethod
    def from_value(cls, value: ToolResult | Mapping[str, Any]) -> ToolResult:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("Invalid tool result envelope")
        ok = value.get("ok")
        tool = value.get("tool")
        run_id = value.get("run_id")
        data = value.get("data", {})
        raw_error = value.get("error")
        if type(ok) is not bool or not isinstance(tool, str) or not isinstance(run_id, str):
            raise ValueError("Invalid tool result envelope")
        if not isinstance(data, Mapping):
            raise ValueError("Invalid tool result data")
        if ok and raw_error is not None:
            raise ValueError("Successful tool result has an error")
        if not ok:
            if not isinstance(raw_error, Mapping):
                raise ValueError("Failed tool result has no error")
            code = raw_error.get("code")
            retriable = raw_error.get("retriable")
            if code not in ERROR_CODES or type(retriable) is not bool:
                raise ValueError("Invalid tool error")
            return cls(
                ok=False,
                tool=tool,
                run_id=run_id,
                data=data,
                error=ToolError(code=code, retriable=retriable),
            )
        return cls(ok=True, tool=tool, run_id=run_id, data=data)


AGENT_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "failed", "needs_user_action"]},
        "run_id": {"type": "string", "format": "uuid"},
        "case_id": {"type": ["string", "null"], "format": "uuid"},
        "summary": {"type": "string", "maxLength": 500},
        "warnings": {
            "type": "array",
            "items": {"type": "string", "maxLength": 240},
            "maxItems": 10,
        },
        "recommended_next_step": {"type": "string", "maxLength": 300},
    },
    "required": ["status", "run_id", "case_id", "summary", "warnings", "recommended_next_step"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class AgentDecision:
    status: Literal["completed", "failed", "needs_user_action"]
    run_id: str
    case_id: str | None
    summary: str
    warnings: tuple[str, ...]
    recommended_next_step: str

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> AgentDecision:
        if not isinstance(value, Mapping) or set(value) != set(AGENT_DECISION_SCHEMA["required"]):
            raise ValueError("Invalid decision fields")
        if value["status"] not in ("completed", "failed", "needs_user_action"):
            raise ValueError("Invalid decision status")
        for key in ("run_id", "summary", "recommended_next_step"):
            if not isinstance(value[key], str):
                raise ValueError("Invalid decision field")
        try:
            UUID(value["run_id"])
            if value["case_id"] is not None:
                if not isinstance(value["case_id"], str):
                    raise ValueError("Invalid case ID")
                UUID(value["case_id"])
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("Invalid decision ID") from exc
        warnings = value["warnings"]
        if (
            not isinstance(warnings, list)
            or len(warnings) > 10
            or any(not isinstance(item, str) or len(item) > 240 for item in warnings)
        ):
            raise ValueError("Invalid decision warnings")
        if len(value["summary"]) > 500 or len(value["recommended_next_step"]) > 300:
            raise ValueError("Decision text too long")
        return cls(
            status=value["status"],
            run_id=value["run_id"],
            case_id=value["case_id"],
            summary=value["summary"],
            warnings=tuple(warnings),
            recommended_next_step=value["recommended_next_step"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "summary": self.summary,
            "warnings": list(self.warnings),
            "recommended_next_step": self.recommended_next_step,
        }


@dataclass(frozen=True, slots=True)
class AgentSettings:
    max_tool_calls: int = 12
    max_tool_attempts: int = 2
    max_invalid_responses: int = 1
    max_timeout_retries: int = 1
    timeout_seconds: float = 30.0
    model: str = "gpt-5-mini"

    def __post_init__(self) -> None:
        if self.max_tool_calls < 1 or self.max_tool_attempts < 1 or self.max_invalid_responses < 0:
            raise ValueError("Invalid agent limits")
        if (
            self.max_timeout_retries < 0
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or not self.model
        ):
            raise ValueError("Invalid provider settings")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AgentSettings:
        values = os.environ if environ is None else environ
        try:
            return cls(
                max_tool_calls=int(values.get("OPENAI_MAX_TOOL_CALLS", "12")),
                timeout_seconds=float(values.get("OPENAI_TIMEOUT_SECONDS", "30")),
                model=values.get("OPENAI_MODEL", "gpt-5-mini"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid agent environment settings") from exc
