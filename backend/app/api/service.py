"""Query and execution services. Routes do not access SQL, files, or NetworkX."""

import asyncio
import hashlib
import logging
from collections import defaultdict

from starlette.concurrency import run_in_threadpool

from ..config import Settings
from .errors import AppError
from .ports import RunBackend, RunExecutor
from .schemas import (
    TERMINAL_STATES,
    ArtifactName,
    EgoGraph,
    ExecuteView,
    NodeDetail,
    Page,
    RunState,
)

logger = logging.getLogger(__name__)


class ApiService:
    def __init__(
        self, settings: Settings, backend: RunBackend | None, executor: RunExecutor | None
    ):
        if (backend is None) != (executor is None):
            raise ValueError("Provide both RunBackend and RunExecutor, or neither")
        self.settings = settings
        self.backend = backend
        self.executor = executor
        self.tasks: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.closing = False
        self.streams = 0

    def require_backend(self) -> RunBackend:
        if self.backend is None:
            raise AppError("BACKEND_NOT_CONFIGURED", "Phase 1–3 backend is not connected.", 503)
        return self.backend

    def health(self) -> dict:
        if self.backend:
            self.backend.check_health()
        return {
            "status": "ok",
            "mode": "demo" if self.settings.demo_mode else "live",
            "backend_ready": self.backend is not None,
            "live_configured": bool(self.settings.openai_api_key.get_secret_value().strip()),
        }

    def create_run(self, request):
        backend = self.require_backend()
        mode = request.mode or ("demo" if self.settings.demo_mode else "live")
        return backend.create_run(
            dataset_id=request.dataset_id,
            mode=mode,
            model=self.settings.openai_model if mode == "live" else None,
        )

    async def start(self, run_id: str) -> ExecuteView:
        backend = self.require_backend()
        async with self.lock:
            run = await run_in_threadpool(backend.get_run, run_id)
            if run.status == RunState.COMPLETED or run.executing:
                return ExecuteView(
                    run_id=run_id, status=run.status, executing=run.executing, started=False
                )
            if run.status in TERMINAL_STATES:
                raise AppError("INVALID_STATE", "Create a new run after a terminal failure.")
            if run.mode == "live" and not self.settings.openai_api_key.get_secret_value().strip():
                raise AppError(
                    "OPENAI_KEY_REQUIRED",
                    "Live execution needs OPENAI_API_KEY; configure it locally or use demo mode.",
                )
            if self.closing or len(self.tasks) >= self.settings.api_max_active_runs:
                raise AppError(
                    "EXECUTION_CAPACITY", "Execution capacity is busy; retry later.", 503
                )
            started = await run_in_threadpool(backend.claim_execution, run_id)
            if started:
                task = asyncio.create_task(self._execute(run_id), name=f"aml-run-{run_id}")
                self.tasks[run_id] = task
            run = await run_in_threadpool(backend.get_run, run_id)
            return ExecuteView(
                run_id=run_id, status=run.status, executing=run.executing, started=started
            )

    async def _execute(self, run_id: str):
        backend = self.require_backend()
        try:
            async with asyncio.timeout(self.settings.api_execution_timeout_seconds):
                await self.executor.execute_run(run_id)
            run = await run_in_threadpool(backend.get_run, run_id)
            if run.status not in TERMINAL_STATES and not (
                run.result and run.result.status == "needs_user_action"
            ):
                await run_in_threadpool(backend.record_failure, run_id, "EXECUTION_INCOMPLETE")
        except asyncio.CancelledError:
            # Preserve the last committed state so a new process can resume it.
            raise
        except Exception as exc:
            code = "EXECUTION_TIMEOUT" if isinstance(exc, TimeoutError) else "EXECUTION_FAILED"
            try:
                await run_in_threadpool(backend.record_failure, run_id, code)
            except Exception:
                logger.error("Could not persist execution failure")
        finally:
            try:
                await run_in_threadpool(backend.release_execution, run_id)
            except Exception:
                logger.error("Could not release execution claim")
            self.tasks.pop(run_id, None)

    async def close(self):
        self.closing = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def reset_demo(self) -> dict:
        backend = self.require_backend()
        if not self.settings.demo_mode:
            raise AppError("DEMO_RESET_DISABLED", "Demo reset is disabled in live mode.", 403)
        async with self.lock:
            if self.tasks or self.streams:
                raise AppError(
                    "RUN_BUSY", "Close event streams and wait for executions before resetting."
                )
            count = await run_in_threadpool(backend.reset_demo)
        return {"deleted_runs": count}

    def nodes(
        self,
        run_id: str,
        *,
        offset: int,
        limit: int,
        role=None,
        cluster_id=None,
        gid=None,
        is_seed=None,
        truncated_by_depth=None,
    ):
        rows = self.require_backend().list_nodes(run_id)
        filters = {
            "role": role,
            "cluster_id": cluster_id,
            "gid": gid,
            "is_seed": is_seed,
            "truncated_by_depth": truncated_by_depth,
        }
        rows = [
            row
            for row in rows
            if all(value is None or getattr(row, key) == value for key, value in filters.items())
        ]
        rows.sort(key=lambda row: (-row.priority_score, -row.role_score, int(row.gid)))
        return Page(
            items=rows[offset : offset + limit], total=len(rows), offset=offset, limit=limit
        )

    def clusters(self, run_id: str, *, offset: int, limit: int):
        rows = sorted(self.require_backend().list_clusters(run_id), key=lambda row: row.cluster_id)
        return Page(
            items=rows[offset : offset + limit], total=len(rows), offset=offset, limit=limit
        )

    def node_detail(
        self, run_id: str, gid: str, *, neighbor_hops: int, max_nodes: int, max_edges: int
    ):
        backend = self.require_backend()
        nodes = {row.gid: row for row in backend.list_nodes(run_id)}
        if gid not in nodes:
            raise AppError("NODE_NOT_FOUND", "Node not found in this run.", 404)
        edges = backend.list_edges(run_id)
        neighbors = defaultdict(set)
        for edge in edges:
            if edge.src not in nodes or edge.dst not in nodes:
                raise AppError(
                    "BACKEND_CONTRACT_ERROR", "Graph references an unavailable assessment.", 500
                )
            neighbors[edge.src].add(edge.dst)
            neighbors[edge.dst].add(edge.src)
        # Radius is measured in either direction; the returned edges retain direction.
        selected, seen, frontier = [gid], {gid}, {gid}
        for _ in range(neighbor_hops):
            frontier = {other for current in frontier for other in neighbors[current]} - seen
            seen.update(frontier)
            selected.extend(sorted(frontier, key=int))
        truncated = len(selected) > max_nodes
        selected = selected[:max_nodes]
        allowed = set(selected)
        selected_edges = sorted(
            (edge for edge in edges if edge.src in allowed and edge.dst in allowed),
            key=lambda edge: (int(edge.src), int(edge.dst)),
        )
        truncated |= len(selected_edges) > max_edges
        cluster = next(
            (
                row
                for row in backend.list_clusters(run_id)
                if row.cluster_id == nodes[gid].cluster_id
            ),
            None,
        )
        if cluster is None:
            raise AppError("BACKEND_CONTRACT_ERROR", "Node cluster is unavailable.", 500)
        graph = EgoGraph(
            center_gid=gid,
            neighbor_hops=neighbor_hops,
            nodes=[nodes[current] for current in selected],
            edges=selected_edges[:max_edges],
            truncated=truncated,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        return NodeDetail(node=nodes[gid], cluster=cluster, ego_graph=graph)

    def artifact(self, run_id: str, name: ArtifactName):
        backend = self.require_backend()
        run = backend.get_run(run_id)
        if run.status != RunState.COMPLETED or run.verification_status != "passed":
            raise AppError(
                "ARTIFACT_NOT_VERIFIED",
                "Downloads require completed, independently verified results.",
            )
        artifact = backend.read_artifact(run_id, name)
        if hashlib.sha256(artifact.content).hexdigest() != artifact.sha256:
            raise AppError("ARTIFACT_INTEGRITY_FAILED", "Artifact integrity check failed.")
        return artifact
