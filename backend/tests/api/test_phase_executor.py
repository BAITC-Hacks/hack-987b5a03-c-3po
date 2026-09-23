"""The HTTP executor uses the real Phase 3 workflow and stops its worker safely."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event

import pytest

from aml_agent.storage import Database, RunMode, RunState
from backend.app.api.phase_executor import PhaseRunExecutor
from backend.app.config import Settings

DATA = Path(__file__).resolve().parents[3] / "data"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        demo_mode=True,
        database_url=f"sqlite:///{tmp_path / 'run.sqlite3'}",
        artifacts_dir=tmp_path / "artifacts",
        data_dir=DATA,
        openai_api_key="",
    )


def test_bundled_demo_reaches_verified_completion(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with Database(settings.database_path) as database:
        run_id = database.create_run("bundled", RunMode.DEMO).run_id

    executor = PhaseRunExecutor(
        settings,
        settings.database_path,
        settings.artifacts_dir,
        settings.data_dir,
    )
    asyncio.run(executor.execute_run(run_id))

    with Database(settings.database_path) as database:
        run = database.require_run(run_id)
        review_case = database.get_review_case_for_run(run_id)
        assert run.status is RunState.COMPLETED
        assert run.verification_status.value == "passed"
        assert review_case is not None and len(review_case.target_gids) == 20
        assert {item.name for item in database.list_artifacts(run_id)} >= {
            "nodes_roles.csv",
            "clusters.csv",
            "top_nodes.csv",
        }


def test_live_missing_key_persists_safe_pause(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with Database(settings.database_path) as database:
        run_id = database.create_run("bundled", RunMode.LIVE).run_id

    executor = PhaseRunExecutor(
        settings,
        settings.database_path,
        settings.artifacts_dir,
        settings.data_dir,
    )
    asyncio.run(executor.execute_run(run_id))

    with Database(settings.database_path) as database:
        run = database.require_run(run_id)
        decision = run.metadata["agent_decision"]
        assert run.status is RunState.CREATED
        assert decision["status"] == "needs_user_action"
        assert decision["run_id"] == run_id
        assert "OPENAI_API_KEY_REQUIRED" in decision["summary"]
        assert len(database.list_events(run_id)) >= 2


def test_cancellation_waits_for_worker_to_stop(tmp_path: Path, monkeypatch) -> None:
    executor = PhaseRunExecutor(_settings(tmp_path), tmp_path / "db", tmp_path / "out", DATA)
    started = Event()
    release = Event()
    stopped = Event()

    async def slow_agent(_run_id, control):
        control.register()
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Simulate a synchronous tool that must finish its current write.
            await asyncio.to_thread(release.wait)
            raise
        finally:
            stopped.set()
            control.unregister()

    monkeypatch.setattr(executor, "_run_agent", slow_agent)

    async def exercise():
        task = asyncio.create_task(executor.execute_run("run"))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert stopped.is_set()

    asyncio.run(exercise())
