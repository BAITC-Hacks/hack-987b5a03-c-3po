"""Integration seam for Phases 1–3; no storage, analytics, or provider implementation.

Adapters return the HTTP DTOs below and translate domain failures to safe AppError
codes. All synchronous methods must be thread-safe. See docs/API.md for invariants.
"""

from dataclasses import dataclass
from typing import Protocol

from .schemas import (
    ArtifactName,
    CaseView,
    ClusterView,
    DatasetImportView,
    EdgeView,
    EventView,
    NodeView,
    RunView,
)


@dataclass(frozen=True)
class ArtifactContent:
    content: bytes
    sha256: str


class RunBackend(Protocol):
    def check_health(self) -> None:
        """Raise on unavailable persistence. Must not return settings or secrets."""
        ...

    def create_run(self, *, dataset_id: str, mode: str, model: str | None) -> RunView: ...

    def import_dataset(
        self, *, files: list[tuple[str, bytes]], seed_gids: list[str]
    ) -> DatasetImportView: ...

    def get_run(self, run_id: str) -> RunView: ...

    def claim_execution(self, run_id: str) -> bool:
        """Atomically claim a resumable run; false for already active/completed runs."""
        ...

    def release_execution(self, run_id: str) -> None: ...

    def record_failure(self, run_id: str, code: str) -> None:
        """Persist safe failure status/event; never overwrite a completed run."""
        ...

    def list_events(self, run_id: str, *, after: int, limit: int) -> list[EventView]:
        """Persisted events only, strictly increasing sequence, exclusive cursor."""
        ...

    def list_nodes(self, run_id: str) -> list[NodeView]:
        """Persisted assessments only; INVALID_STATE (409) until classified."""
        ...

    def list_edges(self, run_id: str) -> list[EdgeView]: ...

    def list_clusters(self, run_id: str) -> list[ClusterView]: ...

    def get_case(self, case_id: str) -> CaseView: ...

    def read_artifact(self, run_id: str, name: ArtifactName) -> ArtifactContent:
        """Read only the owned run's allowlisted file, safely beneath its artifact root."""
        ...

    def reset_demo(self) -> int:
        """Atomically remove only demo state/artifacts; reject if any demo run is active."""
        ...


class RunExecutor(Protocol):
    async def execute_run(self, run_id: str) -> None:
        """Drive registered tools and persist the result; offload CPU/SQLite work.

        Must be cancellation-cooperative and must not return until a terminal or
        needs_user_action result is persisted. Completion requires verify_run.
        """
        ...
