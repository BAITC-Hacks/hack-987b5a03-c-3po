"""Real persistence/analytics coverage for the HTTP integration adapter."""

import asyncio
import hashlib
from pathlib import Path

import pytest

from aml_agent.agent.integration import (
    DatabaseEventStore,
    DatabaseRunRepository,
    RuntimeToolRegistry,
)
from aml_agent.agent.models import AgentSettings
from aml_agent.agent.orchestrator import AgentOrchestrator
from aml_agent.agent.providers import DeterministicDemoProvider
from backend.app.api.errors import AppError
from backend.app.api.phase_adapter import PhaseRunBackend
from backend.app.config import Settings

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


@pytest.fixture
def phase_backend(tmp_path):
    settings = Settings(_env_file=None, app_env="test", demo_mode=True)
    return PhaseRunBackend(settings, tmp_path / "aml.db", tmp_path / "artifacts", DATA_DIR)


def test_real_run_claim_events_and_safe_failure(phase_backend):
    created = phase_backend.create_run(dataset_id="bundled", mode="demo", model=None)
    run_id = str(created.run_id)
    live_id = str(
        phase_backend.create_run(dataset_id="bundled", mode="live", model="gpt-5-mini").run_id
    )
    assert created.status == "created"
    assert phase_backend.claim_execution(run_id) is True
    assert phase_backend.claim_execution(run_id) is False
    assert phase_backend.get_run(run_id).executing is True
    with pytest.raises(AppError) as exc:
        phase_backend.list_nodes(run_id)
    assert exc.value.status_code == 409

    phase_backend.record_failure(run_id, "EXECUTION_TIMEOUT")
    phase_backend.release_execution(run_id)
    failed = phase_backend.get_run(run_id)
    assert failed.status == "failed" and failed.executing is False
    events = phase_backend.list_events(run_id, after=0, limit=100)
    assert len(events) == 1
    assert events[0].payload_json == {"code": "EXECUTION_TIMEOUT"}
    assert phase_backend.claim_execution(run_id) is False
    assert phase_backend.reset_demo() == 1
    assert phase_backend.get_run(live_id).status == "created"
    with pytest.raises(AppError) as exc:
        phase_backend.get_run(run_id)
    assert exc.value.status_code == 404


def test_bundled_verified_run_exposes_csv_backed_read_models(phase_backend):
    created = phase_backend.create_run(dataset_id="bundled", mode="demo", model=None)
    run_id = str(created.run_id)
    assert phase_backend.claim_execution(run_id) is True
    agent = AgentOrchestrator(
        DatabaseRunRepository(phase_backend.database),
        RuntimeToolRegistry(phase_backend.runtime),
        DatabaseEventStore(phase_backend.database),
        DeterministicDemoProvider(),
        settings=AgentSettings(),
    )
    decision = asyncio.run(agent.execute_run(run_id))
    phase_backend.release_execution(run_id)
    assert decision.status == "completed"

    run = phase_backend.get_run(run_id)
    assert run.status == "completed"
    assert run.verification_status == "passed"
    assert run.result.status == "completed"
    assert len(run.verification) >= 19 and all(run.verification.values())
    nodes = phase_backend.list_nodes(run_id)
    assert len(nodes) == 2248
    assert (
        sum(node.is_seed and node.in_degree == 0 and node.out_degree == 0 for node in nodes) == 19
    )
    assert sum(node.truncated_by_depth for node in nodes) == 444
    assert all(isinstance(node.gid, str) for node in nodes)
    assert len(phase_backend.list_edges(run_id)) == 3119
    assert len(phase_backend.list_clusters(run_id)) == 91
    case = phase_backend.get_case(str(run.case_id))
    assert len(case.targets) == 20
    assert [target.gid for target in case.targets] == case.target_gids
    events = phase_backend.list_events(run_id, after=0, limit=1000)
    assert events[-1].kind == "completed"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    artifact = phase_backend.read_artifact(run_id, "nodes_roles.csv")
    assert hashlib.sha256(artifact.content).hexdigest() == artifact.sha256
