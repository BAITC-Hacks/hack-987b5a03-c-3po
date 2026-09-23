"""Storage-domain enums and immutable records for AML Agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class RunMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class RunState(StrEnum):
    CREATED = "created"
    VALIDATED = "validated"
    GRAPH_READY = "graph_ready"
    ANALYZED = "analyzed"
    CLUSTERED = "clustered"
    CLASSIFIED = "classified"
    RANKED = "ranked"
    CASE_CREATED = "case_created"
    EXPORTED = "exported"
    VERIFIED = "verified"
    COMPLETED = "completed"
    VERIFICATION_FAILED = "verification_failed"
    FAILED = "failed"


class VerificationStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class EventKind(StrEnum):
    STARTED = "started"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    DECISION = "decision"
    ACTION = "action"
    WARNING = "warning"
    VERIFICATION = "verification"
    FAILED = "failed"
    COMPLETED = "completed"


class ReviewCaseStatus(StrEnum):
    READY_FOR_REVIEW = "ready_for_review"
    IN_REVIEW = "in_review"
    CLOSED = "closed"


class CaseCreator(StrEnum):
    AGENT = "agent"
    ANALYST = "analyst"


ALLOWED_ARTIFACT_NAMES = frozenset(
    {
        "nodes_roles.csv",
        "clusters.csv",
        "top_nodes.csv",
        "aml_review_report.xlsx",
        "audit.json",
    }
)
MANDATORY_ARTIFACT_NAMES = frozenset(
    {"nodes_roles.csv", "clusters.csv", "top_nodes.csv"}
)
READER_ARTIFACT_NAMES = frozenset({"aml_review_report.xlsx"})


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    run_id: str
    dataset_id: str
    dataset_sha256: str | None
    mode: RunMode
    status: RunState
    ruleset_version: str
    model: str | None
    created_at: datetime
    updated_at: datetime
    warning_count: int
    verification_status: VerificationStatus
    verification_payload: dict[str, Any]
    metadata: dict[str, Any]
    previous_response_id: str | None
    revision: int


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_id: str
    run_id: str
    sequence: int
    kind: EventKind
    tool_name: str | None
    summary: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewCase:
    case_id: str
    run_id: str
    title: str
    status: ReviewCaseStatus
    target_gids: tuple[str, ...]
    created_by: CaseCreator
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewCaseCreateResult:
    case: ReviewCase
    idempotent_replay: bool


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    run_id: str
    name: str
    relative_path: str
    sha256: str
    row_count: int | None
    size_bytes: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ToolResultRecord:
    run_id: str
    tool_name: str
    idempotency_key: str
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ToolCompletion:
    tool_result: ToolResultRecord
    run: AnalysisRun
    idempotent_replay: bool


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    run_id: str
    name: str
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime
