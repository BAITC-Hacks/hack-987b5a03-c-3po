import time

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.api.fakes import FixtureExecutor, MemoryBackend


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        app_env="test",
        openai_api_key="",
        demo_mode=True,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        artifacts_dir=tmp_path / "artifacts",
        sse_poll_seconds=0.01,
        sse_heartbeat_seconds=0.02,
    )


@pytest.fixture
def backend():
    return MemoryBackend()


@pytest.fixture
def executor(backend):
    return FixtureExecutor(backend)


@pytest.fixture
def client(settings, backend, executor):
    with TestClient(
        create_app(settings, backend=backend, executor=executor), raise_server_exceptions=False
    ) as client:
        yield client


def create_and_execute(client, mode="demo"):
    created = client.post("/api/runs", json={"mode": mode})
    assert created.status_code == 201, created.text
    rid = created.json()["run_id"]
    assert client.post(f"/api/runs/{rid}/execute").status_code == 202
    return rid


def wait_finished(client, rid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{rid}").json()
        if not run["executing"] and run["status"] in {"completed", "failed", "verification_failed"}:
            return run
        time.sleep(0.01)
    pytest.fail("Fixture execution did not finish")
