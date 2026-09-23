"""HTTP-only test doubles. Never imported by backend.app or used in DEMO_MODE.

These fixed fixtures intentionally do not load parquet or calculate roles. They
exercise the integration ports while the real Phases 1–3 are built separately.
"""

import asyncio
import csv
import hashlib
import io
import json
import threading
from datetime import UTC, datetime
from uuid import UUID, uuid4

from backend.app.api.errors import AppError
from backend.app.api.ports import ArtifactContent
from backend.app.api.schemas import (
    AgentDecision,
    CaseView,
    ClusterView,
    EdgeView,
    EventView,
    NodeView,
    RankedTarget,
    RunState,
    RunView,
)

GIDS = [str(9007199254741000 + i) for i in range(25)]
NOW = datetime(2026, 7, 31, tzinfo=UTC)


class MemoryBackend:
    def __init__(self):
        self.lock = threading.RLock()
        self.runs = {}
        self.events = {}
        self.nodes = {}
        self.clusters = {}
        self.edges = {}
        self.cases = {}
        self.artifacts = {}
        self.claims = 0
        self.releases = 0
        self.failure_codes = []

    def check_health(self):
        return None

    def create_run(self, *, dataset_id, mode, model):
        run = RunView(
            run_id=uuid4(),
            dataset_id=dataset_id,
            mode=mode,
            model=model,
            created_at=NOW,
            updated_at=NOW,
        )
        with self.lock:
            self.runs[str(run.run_id)] = run
            self.events[str(run.run_id)] = []
        return run.model_copy(deep=True)

    def get_run(self, run_id):
        with self.lock:
            if run_id not in self.runs:
                raise AppError("RUN_NOT_FOUND", "Run not found.", 404)
            return self.runs[run_id].model_copy(deep=True)

    def update_run(self, run_id, **changes):
        with self.lock:
            data = self.get_run(run_id).model_dump()
            self.runs[run_id] = RunView.model_validate(dict(data, **changes))

    def claim_execution(self, run_id):
        with self.lock:
            run = self.get_run(run_id)
            if run.executing or run.status == RunState.COMPLETED:
                return False
            self.claims += 1
            self.update_run(run_id, executing=True)
            self.add_event(run_id, "started", "Fixture execution started.")
            return True

    def release_execution(self, run_id):
        with self.lock:
            self.releases += 1
            self.update_run(run_id, executing=False)

    def record_failure(self, run_id, code):
        with self.lock:
            self.failure_codes.append(code)
            self.update_run(run_id, status=RunState.FAILED)
            self.add_event(run_id, "failed", "Execution failed.", payload={"code": code})

    def add_event(self, run_id, kind, summary, payload=None):
        with self.lock:
            self.events[run_id].append(
                EventView(
                    event_id=uuid4(),
                    run_id=run_id,
                    sequence=len(self.events[run_id]) + 1,
                    kind=kind,
                    summary=summary,
                    payload_json=payload or {},
                    created_at=NOW,
                )
            )

    def list_events(self, run_id, *, after, limit):
        with self.lock:
            self.get_run(run_id)
            return [e.model_copy(deep=True) for e in self.events[run_id] if e.sequence > after][
                :limit
            ]

    def list_nodes(self, run_id):
        with self.lock:
            self.get_run(run_id)
            if run_id not in self.nodes:
                raise AppError("INVALID_STATE", "Assessments are not yet available.")
            return [row.model_copy(deep=True) for row in self.nodes[run_id]]

    def list_edges(self, run_id):
        self.get_run(run_id)
        return self.edges[run_id][:]

    def list_clusters(self, run_id):
        self.list_nodes(run_id)
        return self.clusters[run_id][:]

    def get_case(self, case_id):
        with self.lock:
            if case_id not in self.cases:
                raise AppError("CASE_NOT_FOUND", "Review case not found.", 404)
            return self.cases[case_id].model_copy(deep=True)

    def read_artifact(self, run_id, name):
        self.get_run(run_id)
        if (run_id, name) not in self.artifacts:
            raise AppError("ARTIFACT_NOT_FOUND", "Artifact not found.", 404)
        return self.artifacts[run_id, name]

    def reset_demo(self):
        with self.lock:
            ids = {rid for rid, run in self.runs.items() if run.mode == "demo"}
            if any(self.runs[rid].executing for rid in ids):
                raise AppError("RUN_BUSY", "Demo run is active.")
            for rid in ids:
                for collection in (self.runs, self.events, self.nodes, self.clusters, self.edges):
                    collection.pop(rid, None)
            self.cases = {
                cid: case for cid, case in self.cases.items() if str(case.run_id) not in ids
            }
            self.artifacts = {
                key: value for key, value in self.artifacts.items() if key[0] not in ids
            }
            return len(ids)

    def finish_fixture(self, run_id, verification_passed=True):
        with self.lock:
            nodes = [
                NodeView(
                    run_id=run_id,
                    gid=gid,
                    depth=4 if i % 5 == 0 else 1,
                    is_seed=i == 1,
                    in_degree=2,
                    out_degree=1,
                    in_kzt=10000,
                    out_kzt=9000,
                    in_tx=2,
                    out_tx=1,
                    pass_through_ratio=None if i == 1 else 0.9,
                    pagerank=0.01,
                    betweenness=0.01,
                    seed_reach_count=1,
                    rapid_outflow_ratio=None,
                    truncated_by_depth=i % 5 == 0,
                    uncertainty_flags=["sampled_network"],
                    role="peripheral",
                    role_score=0.8,
                    cluster_id=i % 2,
                    priority_score=round(0.9 - (i // 2) * 0.01, 6),
                    evidence=f"Observed {i + 1} counterparties; fixture for review.",
                )
                for i, gid in enumerate(GIDS)
            ]
            self.nodes[run_id] = list(reversed(nodes))
            self.clusters[run_id] = [
                ClusterView(
                    run_id=run_id,
                    cluster_id=i,
                    n_nodes=sum(n.cluster_id == i for n in nodes),
                    n_seed=int(i == 1),
                    sum_kzt_internal=10000,
                    top_gids=[n.gid for n in nodes if n.cluster_id == i][:5],
                    hypothesis="Fixture transfer community for analyst review.",
                )
                for i in (0, 1)
            ]
            pairs = [(0, 1), (1, 2), (2, 4), (3, 0), (0, 5), (5, 6)]
            self.edges[run_id] = [
                EdgeView(src=GIDS[a], dst=GIDS[b], sum_kzt=5000, n_tx=1) for a, b in pairs
            ]
            targets = [
                RankedTarget(
                    rank=i, gid=n.gid, role=n.role, priority_score=n.priority_score, why=n.evidence
                )
                for i, n in enumerate(nodes[:20], 1)
            ]
            case = CaseView(
                case_id=uuid4(),
                run_id=run_id,
                title="Fixture review case",
                target_gids=[n.gid for n in targets],
                targets=targets,
                created_at=NOW,
            )
            self.cases[str(case.case_id)] = case
            for name, rows, columns in (
                (
                    "nodes_roles.csv",
                    nodes,
                    ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"],
                ),
                (
                    "clusters.csv",
                    self.clusters[run_id],
                    [
                        "cluster_id",
                        "n_nodes",
                        "n_seed",
                        "sum_kzt_internal",
                        "top_gids",
                        "hypothesis",
                    ],
                ),
                ("top_nodes.csv", targets, ["rank", "gid", "role", "priority_score", "why"]),
            ):
                stream = io.StringIO(newline="")
                writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(row.model_dump(mode="json") for row in rows)
                content = stream.getvalue().encode()
                self.artifacts[run_id, name] = ArtifactContent(
                    content, hashlib.sha256(content).hexdigest()
                )
            content = json.dumps(
                {"run_id": run_id, "passed": verification_passed, "fixture": True}
            ).encode()
            self.artifacts[run_id, "audit.json"] = ArtifactContent(
                content, hashlib.sha256(content).hexdigest()
            )
            content = b"PK\x03\x04fixture-workbook"
            self.artifacts[run_id, "aml_review_report.xlsx"] = ArtifactContent(
                content, hashlib.sha256(content).hexdigest()
            )
            self.add_event(
                run_id, "action", "Fixture case created.", {"case_id": str(case.case_id)}
            )
            self.add_event(
                run_id,
                "verification",
                "Fixture verification result.",
                {"passed": verification_passed},
            )
            status = RunState.COMPLETED if verification_passed else RunState.VERIFICATION_FAILED
            decision = AgentDecision(
                status="completed" if verification_passed else "failed",
                run_id=UUID(run_id),
                case_id=case.case_id,
                summary="Fixture HTTP workflow finished.",
                warnings=["Test fixture; no analytical computation."],
                recommended_next_step="Review fixture.",
            )
            self.update_run(
                run_id,
                status=status,
                verification_status="passed" if verification_passed else "failed",
                case_id=case.case_id,
                counts={"n_nodes": 25, "n_clusters": 2},
                result=decision,
            )
            self.add_event(
                run_id, "completed" if verification_passed else "failed", "Fixture finished."
            )


class FixtureExecutor:
    def __init__(self, backend):
        self.backend = backend
        self.calls = 0
        self.gate = threading.Event()
        self.gate.set()
        self.fail = False
        self.verification_passed = True

    async def execute_run(self, run_id):
        self.calls += 1
        while not self.gate.is_set():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)
        if self.fail:
            raise RuntimeError("secret-fixture-key and internal filesystem path must never leak")
        self.backend.finish_fixture(run_id, self.verification_passed)
