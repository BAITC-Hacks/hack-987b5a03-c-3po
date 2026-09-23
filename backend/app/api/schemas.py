"""HTTP representations, separate from the domain models owned by Phases 1–3."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)


def validate_gid(value: str) -> str:
    if int(value) > 2**63 - 1:
        raise ValueError("GID exceeds int64")
    return value


Gid = Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$"), AfterValidator(validate_gid)]
Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Role = Literal["coordinator", "consolidator", "distributor", "transit", "terminal", "peripheral"]
ArtifactName = Literal[
    "nodes_roles.csv",
    "clusters.csv",
    "top_nodes.csv",
    "aml_review_report.xlsx",
    "audit.json",
]
ARTIFACT_NAMES = (
    "nodes_roles.csv",
    "clusters.csv",
    "top_nodes.csv",
    "aml_review_report.xlsx",
    "audit.json",
)
ToolName = Literal[
    "inspect_dataset",
    "build_graph",
    "compute_graph_features",
    "cluster_network",
    "assign_roles",
    "rank_targets",
    "get_node_evidence",
    "create_review_case",
    "export_results",
    "verify_run",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


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
    FAILED = "failed"
    VERIFICATION_FAILED = "verification_failed"


TERMINAL_STATES = {RunState.COMPLETED, RunState.FAILED, RunState.VERIFICATION_FAILED}


DatasetId = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]


class CreateRun(Contract):
    dataset_id: DatasetId = "bundled"
    mode: Literal["demo", "live"] | None = None


class DatasetImportView(Contract):
    dataset_id: DatasetId
    n_files: int = Field(ge=1)
    n_transactions: int = Field(ge=1)
    n_nodes: int = Field(ge=1)
    n_edges: int = Field(ge=1)
    n_seed: int = Field(ge=1)
    period_start: str
    period_end: str
    warnings: list[str] = Field(default_factory=list)


class CsvUpload(Contract):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=25 * 1024 * 1024)


class DatasetImportRequest(Contract):
    files: list[CsvUpload] = Field(min_length=1, max_length=12)
    seed_gids: list[Gid] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def total_upload_limit(self):
        if sum(len(file.content.encode("utf-8")) for file in self.files) > 25 * 1024 * 1024:
            raise ValueError("CSV upload exceeds the 25 MB limit")
        return self


class AgentDecision(Contract):
    status: Literal["completed", "failed", "needs_user_action"]
    run_id: UUID
    case_id: UUID | None
    summary: str = Field(max_length=500)
    warnings: list[Annotated[str, Field(max_length=240)]] = Field(max_length=10)
    recommended_next_step: str = Field(max_length=300)


class RunView(Contract):
    run_id: UUID
    dataset_id: str
    dataset_sha256: str | None = None
    mode: Literal["demo", "live"]
    status: RunState = RunState.CREATED
    executing: bool = False
    ruleset_version: Literal["v1"] = "v1"
    model: str | None = None
    created_at: datetime
    updated_at: datetime
    warning_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    verification_status: Literal["pending", "passed", "failed"] = "pending"
    verification: dict[str, bool] = Field(default_factory=dict)
    case_id: UUID | None = None
    result: AgentDecision | None = None

    @model_validator(mode="after")
    def completed_requires_verification(self):
        if self.status == RunState.COMPLETED and self.verification_status != "passed":
            raise ValueError("Completed runs require independent verification")
        if self.result and self.result.status == "completed" and self.status != RunState.COMPLETED:
            raise ValueError("A completed decision requires a completed run")
        return self


class NodeView(Contract):
    run_id: UUID
    gid: Gid
    depth: int = Field(ge=0, le=4)
    is_seed: bool
    in_degree: int = Field(ge=0)
    out_degree: int = Field(ge=0)
    in_kzt: Amount
    out_kzt: Amount
    in_tx: int = Field(ge=0)
    out_tx: int = Field(ge=0)
    pass_through_ratio: Amount | None
    pagerank: Amount
    betweenness: Amount
    seed_reach_count: int = Field(ge=0)
    rapid_outflow_ratio: Score | None
    truncated_by_depth: bool
    uncertainty_flags: list[str]
    role: Role
    role_score: Score
    cluster_id: int = Field(ge=0)
    priority_score: Score
    evidence: str = Field(min_length=1, max_length=200)
    ruleset_version: Literal["v1"] = "v1"


class ClusterView(Contract):
    run_id: UUID
    cluster_id: int
    n_nodes: int = Field(gt=0)
    n_seed: int = Field(ge=0)
    sum_kzt_internal: Amount
    top_gids: list[Gid]
    hypothesis: str
    algorithm: Literal["louvain"] = "louvain"
    random_seed: Literal[42] = 42


class RankedTarget(Contract):
    rank: int = Field(ge=1)
    gid: Gid
    role: Role
    priority_score: Score
    why: str = Field(min_length=1, max_length=200)


class CaseView(Contract):
    case_id: UUID
    run_id: UUID
    title: str
    status: Literal["ready_for_review", "in_review", "closed"] = "ready_for_review"
    target_gids: list[Gid]
    targets: list[RankedTarget]
    created_by: Literal["agent", "analyst"] = "agent"
    created_at: datetime


class EventView(Contract):
    event_id: UUID
    run_id: UUID
    sequence: int = Field(ge=1)
    kind: Literal[
        "started",
        "tool_started",
        "tool_completed",
        "decision",
        "action",
        "warning",
        "verification",
        "failed",
        "completed",
    ]
    tool_name: ToolName | None = None
    summary: str = Field(max_length=500)
    payload_json: dict[str, str | int | bool | list[str]] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("payload_json")
    @classmethod
    def allow_safe_metadata(cls, value):
        allowed = {
            "state",
            "passed",
            "case_id",
            "target_count",
            "assigned_count",
            "code",
            "n_nodes",
            "n_edges",
            "artifact_names",
            "failed_checks",
        }
        if value.keys() - allowed:
            raise ValueError("Unsafe event metadata")
        return value


class EdgeView(Contract):
    src: Gid
    dst: Gid
    sum_kzt: Amount
    n_tx: int = Field(ge=1)


class EgoGraph(Contract):
    center_gid: Gid
    neighbor_hops: int
    directed: Literal[True] = True
    nodes: list[NodeView]
    edges: list[EdgeView]
    truncated: bool
    max_nodes: int
    max_edges: int


class NodeDetail(Contract):
    node: NodeView
    cluster: ClusterView
    ego_graph: EgoGraph


class Page[T](Contract):
    items: list[T]
    total: int
    offset: int
    limit: int


class ExecuteView(Contract):
    run_id: UUID
    status: RunState
    executing: bool
    started: bool


class HealthView(Contract):
    status: Literal["ok"]
    mode: Literal["demo", "live"]
    backend_ready: bool
    live_configured: bool


class ResetView(Contract):
    deleted_runs: int = Field(ge=0)
