"""Run the Phase 3 agent behind the Phase 4 asynchronous execution port."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock

from aml_agent.agent import AgentOrchestrator, AgentSettings
from aml_agent.agent.integration import (
    DatabaseEventStore,
    DatabaseRunRepository,
    RuntimeToolRegistry,
)
from aml_agent.agent.providers import make_provider
from aml_agent.storage import ArtifactStore, Database
from aml_agent.tool_runtime import ToolRuntime

from ..config import Settings


class _WorkerControl:
    """Deliver cancellation to the worker loop, including during startup."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._cancel_requested = False

    def register(self) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        assert task is not None
        with self._lock:
            self._loop = loop
            self._task = task
            cancel_requested = self._cancel_requested
        if cancel_requested:
            task.cancel()

    def unregister(self) -> None:
        with self._lock:
            self._loop = None
            self._task = None

    def cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
            loop = self._loop
            task = self._task
            if loop is not None and task is not None and not loop.is_closed():
                loop.call_soon_threadsafe(task.cancel)


class PhaseRunExecutor:
    """Execute registered tools without blocking the HTTP event loop.

    The worker owns its SQLite wrapper and in-memory tool cache for one run. A
    cancelled API task waits for that worker to stop before ApiService can release
    the run claim. This matters because analytics tools have synchronous writes.
    """

    def __init__(
        self,
        settings: Settings,
        database_path: Path,
        artifacts_dir: Path,
        data_dir: Path,
    ) -> None:
        self.settings = settings
        self.database_path = Path(database_path)
        self.artifacts_dir = Path(artifacts_dir)
        self.data_dir = Path(data_dir)

    async def execute_run(self, run_id: str) -> None:
        control = _WorkerControl()
        worker = asyncio.create_task(asyncio.to_thread(self._run_worker, run_id, control))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            control.cancel()
            # asyncio.to_thread does not stop the underlying thread on its own.
            # Await it even under repeated cancellation so no write outlives the
            # execution claim held by ApiService.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    control.cancel()
                except Exception:
                    break
            raise

    def _run_worker(self, run_id: str, control: _WorkerControl) -> None:
        asyncio.run(self._run_agent(run_id, control))

    async def _run_agent(self, run_id: str, control: _WorkerControl) -> None:
        control.register()
        try:
            with Database(self.database_path) as database:
                run = database.require_run(run_id)
                if "agent_decision" in run.metadata:
                    metadata = dict(run.metadata)
                    metadata.pop("agent_decision")
                    database.update_run(run_id, metadata=metadata)

                agent_settings = AgentSettings(
                    max_tool_calls=self.settings.openai_max_tool_calls,
                    timeout_seconds=self.settings.openai_timeout_seconds,
                    model=run.model or self.settings.openai_model,
                )
                runtime = ToolRuntime(
                    database,
                    ArtifactStore(self.artifacts_dir),
                    {"bundled": self.data_dir},
                    expected_seed_counts={"bundled": 81},
                    expected_periods={"bundled": ("2026-07-01", "2026-07-31")},
                )
                agent = AgentOrchestrator(
                    DatabaseRunRepository(database),
                    RuntimeToolRegistry(runtime),
                    DatabaseEventStore(database),
                    make_provider(
                        run.mode.value,
                        agent_settings,
                        api_key=(
                            self.settings.openai_api_key.get_secret_value()
                            if run.mode.value == "live"
                            else None
                        ),
                    ),
                    settings=agent_settings,
                )
                decision = await agent.execute_run(run_id)
                if decision.status == "needs_user_action":
                    current = database.require_run(run_id)
                    database.update_run(
                        run_id,
                        metadata={**current.metadata, "agent_decision": decision.as_dict()},
                    )
        finally:
            control.unregister()
