"""Bridge the Phase 1–3 repositories and artifacts to the public HTTP port.

The analytical result is read from the verified export bundle. Structural
features are recomputed by the public deterministic analytics library against
the exact dataset fingerprint; this adapter never invents a role or score.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import shutil
import threading
from pathlib import Path
from uuid import UUID, uuid4

from aml_agent.agent.integration import DatabaseRunRepository
from aml_agent.agent.providers import deterministic_decision
from aml_agent.analytics import build_directed_graph, compute_node_features, load_dataset
from aml_agent.analytics.exports import CLUSTERS_COLUMNS, NODES_ROLES_COLUMNS, TOP_NODES_COLUMNS
from aml_agent.analytics.validation import DatasetValidationError
from aml_agent.storage import ArtifactStore, Database, RecordNotFoundError, RunState
from aml_agent.tool_runtime import ToolRuntime

from ..config import Settings
from .dataset_import import import_csv_dataset
from .errors import AppError
from .ports import ArtifactContent
from .schemas import (
    ARTIFACT_NAMES,
    AgentDecision,
    CaseView,
    ClusterView,
    DatasetImportView,
    EdgeView,
    EventView,
    NodeView,
    RankedTarget,
    RunView,
)

_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_EVENT_KEYS = frozenset(
    {"state", "passed", "case_id", "target_count", "assigned_count", "code", "n_nodes", "n_edges"}
)


class PhaseRunBackend:
    """Concrete single-worker backend for the bundled dataset.

    Execution claims are process-local and protected by a lock. The documented
    one-worker deployment clears stale claims on process restart while keeping
    all analytical state, tool results, and events in SQLite.
    """

    def __init__(
        self,
        settings: Settings,
        database_path: Path,
        artifacts_dir: Path,
        data_dir: Path,
        uploads_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.database = Database(database_path)
        self.artifact_store = ArtifactStore(artifacts_dir)
        self.data_dir = Path(data_dir).resolve()
        self.uploads_dir = Path(uploads_dir or (Path(artifacts_dir) / "_datasets")).resolve()
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.runtime = self._make_runtime()
        self.repository = DatabaseRunRepository(self.database)
        self._lock = threading.RLock()
        self._claimed: set[str] = set()

    def check_health(self) -> None:
        with self.database.transaction(immediate=False) as connection:
            connection.execute("SELECT 1").fetchone()
        if not all(
            (self.data_dir / f"{name}.parquet").is_file()
            for name in ("nodes", "edges", "transactions")
        ):
            raise RuntimeError("Bundled dataset unavailable")

    def _require_run(self, run_id: str):
        try:
            return self.database.require_run(run_id)
        except RecordNotFoundError:
            raise AppError("RUN_NOT_FOUND", "Run not found.", 404) from None

    def create_run(self, *, dataset_id: str, mode: str, model: str | None) -> RunView:
        if self._dataset_path(dataset_id) is None:
            raise AppError("INVALID_DATASET", "Dataset is not registered.", 422)
        self.runtime = self._make_runtime()
        return self.get_run(self.runtime.create_run(dataset_id, mode, model=model).run_id)

    def import_dataset(
        self, *, files: list[tuple[str, bytes]], seed_gids: list[str]
    ) -> DatasetImportView:
        dataset_id = f"upload-{uuid4().hex}"
        target = self.uploads_dir / dataset_id
        try:
            values = import_csv_dataset(files, seed_gids, target)
        except DatasetValidationError as exc:
            raise AppError("INVALID_DATASET", str(exc), 422) from None
        except Exception:
            raise AppError(
                "DATASET_IMPORT_FAILED", "The CSV dataset could not be imported.", 500
            ) from None
        with self._lock:
            self.runtime = self._make_runtime()
        return DatasetImportView(dataset_id=dataset_id, **values)

    def get_run(self, run_id: str) -> RunView:
        run = self._require_run(run_id)
        context = self.repository.get_compact_context(run_id)
        verification = {
            item["name"]: item["passed"]
            for item in run.verification_payload.get("checks", ())
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and type(item.get("passed")) is bool
        }
        result = None
        if run.status is RunState.COMPLETED:
            result = AgentDecision.model_validate(deterministic_decision(context).as_dict())
        elif run.status in (RunState.FAILED, RunState.VERIFICATION_FAILED):
            result = AgentDecision(
                status="failed",
                run_id=run_id,
                case_id=context.case_id,
                summary="Run stopped; review the execution events.",
                warnings=list(context.warnings),
                recommended_next_step="Review the run events before starting a new run.",
            )
        else:
            pending_decision = run.metadata.get("agent_decision")
            if (
                isinstance(pending_decision, dict)
                and pending_decision.get("status") == "needs_user_action"
            ):
                result = AgentDecision.model_validate(pending_decision)
        with self._lock:
            executing = run_id in self._claimed
        return RunView(
            run_id=run.run_id,
            dataset_id=run.dataset_id,
            dataset_sha256=run.dataset_sha256,
            mode=run.mode.value,
            status=run.status.value,
            executing=executing,
            ruleset_version=run.ruleset_version,
            model=run.model,
            created_at=run.created_at,
            updated_at=run.updated_at,
            warning_count=run.warning_count,
            warnings=list(context.warnings),
            counts=dict(context.counts),
            verification_status=run.verification_status.value,
            verification=verification,
            case_id=context.case_id,
            result=result,
        )

    def claim_execution(self, run_id: str) -> bool:
        with self._lock:
            run = self._require_run(run_id)
            if run.status in (RunState.COMPLETED, RunState.FAILED, RunState.VERIFICATION_FAILED):
                return False
            if run_id in self._claimed:
                return False
            self._claimed.add(run_id)
            return True

    def release_execution(self, run_id: str) -> None:
        with self._lock:
            self._claimed.discard(run_id)

    def record_failure(self, run_id: str, code: str) -> None:
        run = self._require_run(run_id)
        if run.status in (RunState.COMPLETED, RunState.FAILED, RunState.VERIFICATION_FAILED):
            return
        safe_code = code if _CODE.fullmatch(code) else "EXECUTION_FAILED"
        self.database.append_event(
            run_id, "failed", "Execution stopped safely.", payload={"error_code": safe_code}
        )
        self.database.transition_run(run_id, RunState.FAILED)

    def list_events(self, run_id: str, *, after: int, limit: int) -> list[EventView]:
        self._require_run(run_id)
        events = self.database.list_events(run_id, after_sequence=after, limit=limit)
        result = []
        for event in events:
            payload = {}
            for key, value in event.payload.items():
                mapped = {"error_code": "code", "verification_passed": "passed"}.get(key, key)
                if mapped not in _EVENT_KEYS:
                    continue
                if type(value) in (str, int, bool) and (
                    not isinstance(value, str) or len(value) <= 80
                ):
                    payload[mapped] = value
            artifact_names = event.payload.get("artifact_names")
            if isinstance(artifact_names, list) and all(
                isinstance(name, str) and name in ARTIFACT_NAMES for name in artifact_names
            ):
                payload["artifact_names"] = artifact_names
            result.append(
                EventView(
                    event_id=event.event_id,
                    run_id=event.run_id,
                    sequence=event.sequence,
                    kind=event.kind.value,
                    tool_name=event.tool_name,
                    summary=event.summary[:500],
                    payload_json=payload,
                    created_at=event.created_at,
                )
            )
        return result

    def _ready(self, run_id: str):
        run = self._require_run(run_id)
        if run.status is not RunState.COMPLETED or run.verification_status.value != "passed":
            raise AppError("INVALID_STATE", "Verified assessments are not yet available.", 409)
        return run

    def _verified_csv(
        self, run_id: str, name: str, columns: tuple[str, ...]
    ) -> list[dict[str, str]]:
        artifact = self.read_artifact(run_id, name)
        try:
            reader = csv.DictReader(io.StringIO(artifact.content.decode("utf-8")))
            if tuple(reader.fieldnames or ()) != columns:
                raise ValueError("Unexpected CSV schema")
            rows = list(reader)
            if any(None in row for row in rows):
                raise ValueError("Malformed CSV row")
            return rows
        except (UnicodeError, csv.Error, ValueError):
            raise AppError(
                "ARTIFACT_INTEGRITY_FAILED", "Exported data is malformed.", 409
            ) from None

    def _dataset(self, run_id: str):
        run = self._ready(run_id)
        path = self._dataset_path(run.dataset_id)
        if path is None:
            raise AppError("DATASET_UNAVAILABLE", "Run dataset is unavailable.", 503)
        try:
            dataset = load_dataset(
                path, expected_seed_count=81 if run.dataset_id == "bundled" else None
            )
        except Exception:
            raise AppError("DATASET_UNAVAILABLE", "Run dataset could not be read.", 503) from None
        if dataset.sha256 != run.dataset_sha256:
            raise AppError("DATASET_CHANGED", "Run dataset changed after the run.", 409)
        return dataset

    def _dataset_path(self, dataset_id: str) -> Path | None:
        if dataset_id == "bundled":
            return self.data_dir
        if not re.fullmatch(r"upload-[0-9a-f]{32}", dataset_id):
            return None
        path = (self.uploads_dir / dataset_id).resolve(strict=False)
        if path.parent != self.uploads_dir or not path.is_dir():
            return None
        required = ("nodes", "edges", "transactions")
        if not all((path / f"{name}.parquet").is_file() for name in required):
            return None
        return path

    def _make_runtime(self) -> ToolRuntime:
        registry = {"bundled": self.data_dir}
        if self.uploads_dir.is_dir():
            for path in self.uploads_dir.iterdir():
                dataset = self._dataset_path(path.name)
                if dataset is not None:
                    registry[path.name] = dataset
        return ToolRuntime(
            self.database,
            self.artifact_store,
            registry,
            expected_seed_counts={"bundled": 81},
            expected_periods={"bundled": ("2026-07-01", "2026-07-31")},
        )

    def list_nodes(self, run_id: str) -> list[NodeView]:
        dataset = self._dataset(run_id)
        rows = self._verified_csv(run_id, "nodes_roles.csv", NODES_ROLES_COLUMNS)
        features = compute_node_features(
            dataset, build_directed_graph(dataset.nodes, dataset.edges)
        )
        temporal = self.database.get_snapshot(run_id, "tool_args:compute_graph_features")
        temporal_enabled = temporal is None or temporal.payload.get("include_temporal") is not False
        by_gid = {str(int(row.gid)): row for row in features.itertuples(index=False)}
        if len(rows) != len(by_gid) or {row["gid"] for row in rows} != set(by_gid):
            raise AppError("BACKEND_CONTRACT_ERROR", "Assessment coverage is inconsistent.", 500)
        result = []
        for row in rows:
            feature = by_gid[row["gid"]]
            flags = list(feature.uncertainty_flags)
            rapid = _finite_or_none(feature.rapid_outflow_ratio)
            if not temporal_enabled:
                rapid = None
                flags = [
                    flag
                    for flag in flags
                    if flag not in {"date_only_resolution", "temporal_signal_unavailable"}
                ] + ["temporal_signal_disabled"]
            result.append(
                NodeView(
                    run_id=run_id,
                    gid=row["gid"],
                    depth=int(feature.depth),
                    is_seed=bool(feature.is_seed),
                    in_degree=int(feature.in_degree),
                    out_degree=int(feature.out_degree),
                    in_kzt=float(feature.in_kzt),
                    out_kzt=float(feature.out_kzt),
                    in_tx=int(feature.in_tx),
                    out_tx=int(feature.out_tx),
                    pass_through_ratio=_finite_or_none(feature.pass_through_ratio),
                    pagerank=float(feature.pagerank),
                    betweenness=float(feature.betweenness),
                    seed_reach_count=int(feature.seed_reach_count),
                    rapid_outflow_ratio=rapid,
                    truncated_by_depth=bool(feature.truncated_by_depth),
                    uncertainty_flags=flags,
                    role=row["role"],
                    role_score=float(row["role_score"]),
                    cluster_id=int(row["cluster_id"]),
                    priority_score=float(row["priority_score"]),
                    evidence=row["evidence"],
                    ruleset_version="v1",
                )
            )
        return result

    def list_edges(self, run_id: str) -> list[EdgeView]:
        dataset = self._dataset(run_id)
        return [
            EdgeView(
                src=str(int(row.src)),
                dst=str(int(row.dst)),
                sum_kzt=float(row.sum_kzt),
                n_tx=int(row.n_tx),
            )
            for row in dataset.edges.itertuples(index=False)
        ]

    def list_clusters(self, run_id: str) -> list[ClusterView]:
        self._ready(run_id)
        return [
            ClusterView(
                run_id=run_id,
                cluster_id=int(row["cluster_id"]),
                n_nodes=int(row["n_nodes"]),
                n_seed=int(row["n_seed"]),
                sum_kzt_internal=float(row["sum_kzt_internal"]),
                top_gids=json.loads(row["top_gids"]),
                hypothesis=row["hypothesis"],
            )
            for row in self._verified_csv(run_id, "clusters.csv", CLUSTERS_COLUMNS)
        ]

    def get_case(self, case_id: str) -> CaseView:
        case = self.database.get_review_case(case_id)
        if case is None:
            raise AppError("CASE_NOT_FOUND", "Review case not found.", 404)
        self._ready(case.run_id)
        targets = [
            RankedTarget(
                rank=int(row["rank"]),
                gid=row["gid"],
                role=row["role"],
                priority_score=float(row["priority_score"]),
                why=row["why"],
            )
            for row in self._verified_csv(case.run_id, "top_nodes.csv", TOP_NODES_COLUMNS)
        ]
        if [target.gid for target in targets] != list(case.target_gids):
            raise AppError("BACKEND_CONTRACT_ERROR", "Case ranking is inconsistent.", 500)
        return CaseView(
            case_id=case.case_id,
            run_id=case.run_id,
            title=case.title,
            status=case.status.value,
            target_gids=list(case.target_gids),
            targets=targets,
            created_by=case.created_by.value,
            created_at=case.created_at,
        )

    def read_artifact(self, run_id: str, name: str) -> ArtifactContent:
        self._ready(run_id)
        if name not in ARTIFACT_NAMES:
            raise AppError("ARTIFACT_NOT_FOUND", "Artifact not found.", 404)
        record = self.database.get_artifact(run_id, name)
        if record is None:
            raise AppError("ARTIFACT_NOT_FOUND", "Artifact not found.", 404)
        try:
            content = self.artifact_store.read_bytes(run_id, name)
        except Exception:
            raise AppError(
                "ARTIFACT_INTEGRITY_FAILED", "Artifact could not be read.", 409
            ) from None
        if hashlib.sha256(content).hexdigest() != record.sha256:
            raise AppError("ARTIFACT_INTEGRITY_FAILED", "Artifact integrity check failed.", 409)
        return ArtifactContent(content=content, sha256=record.sha256)

    def reset_demo(self) -> int:
        with self._lock:
            with self.database.transaction() as connection:
                rows = connection.execute(
                    "SELECT run_id, dataset_id FROM analysis_runs WHERE mode = 'demo'"
                ).fetchall()
                run_ids = [row["run_id"] for row in rows]
                uploaded_ids = {
                    row["dataset_id"]
                    for row in rows
                    if re.fullmatch(r"upload-[0-9a-f]{32}", row["dataset_id"])
                }
                if any(run_id in self._claimed for run_id in run_ids):
                    raise AppError("RUN_BUSY", "A demo run is executing.", 409)
                root = self.artifact_store.root
                for run_id in run_ids:
                    UUID(run_id)
                    path = root / run_id
                    resolved = path.resolve(strict=False)
                    if not resolved.is_relative_to(root) or resolved.parent != root:
                        raise AppError("ARTIFACT_INTEGRITY_FAILED", "Artifact path is unsafe.", 409)
                connection.executemany(
                    "DELETE FROM analysis_runs WHERE run_id = ?", ((run_id,) for run_id in run_ids)
                )
                removable_datasets = [
                    dataset_id
                    for dataset_id in uploaded_ids
                    if connection.execute(
                        "SELECT 1 FROM analysis_runs WHERE dataset_id = ? LIMIT 1", (dataset_id,)
                    ).fetchone()
                    is None
                ]
            for run_id in run_ids:
                path = root / run_id
                if path.exists():
                    shutil.rmtree(path)
            for dataset_id in removable_datasets:
                dataset_path = self._dataset_path(dataset_id)
                if dataset_path is not None:
                    shutil.rmtree(dataset_path)
            self.runtime = self._make_runtime()
            return len(run_ids)


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
