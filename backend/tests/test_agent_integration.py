"""Phase 3 contract against the actual Phase 1–2 runtime and bundled data."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from aml_agent.agent import AgentOrchestrator, AgentSettings
from aml_agent.agent.integration import (
    DatabaseEventStore,
    DatabaseRunRepository,
    RuntimeToolRegistry,
)
from aml_agent.agent.providers import (
    DeterministicDemoProvider,
    OpenAIResponsesProvider,
    deterministic_decision,
)
from aml_agent.storage import ArtifactStore, Database, EventKind, RunMode, RunState
from aml_agent.tool_runtime import ToolRuntime
from aml_agent.tools import get_allowed_tool_definitions

DATA = Path(__file__).resolve().parents[2] / "data"


class ScriptedResponses:
    """Fake SDK transport, real Responses adapter and production tools."""

    def __init__(self, repository: DatabaseRunRepository, run_id: str) -> None:
        self.repository = repository
        self.run_id = run_id
        self.requests: list[dict] = []
        self.demo = DeterministicDemoProvider()

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        context = self.repository.get_compact_context(self.run_id)
        if "text" in kwargs:
            decision = deterministic_decision(context)
            return SimpleNamespace(
                id=f"response-{len(self.requests)}",
                status="completed",
                output=(),
                output_text=json.dumps(decision.as_dict()),
            )
        proposal = await self.demo.respond(
            context,
            get_allowed_tool_definitions(context.state),
            None,
        )
        call = proposal.calls[0]
        return SimpleNamespace(
            id=f"response-{len(self.requests)}",
            status="completed",
            output_text=None,
            output=(
                SimpleNamespace(
                    type="function_call",
                    call_id=f"call-{len(self.requests)}",
                    name=call.name,
                    arguments=json.dumps(call.arguments),
                ),
            ),
        )


def _execute_mode(tmp_path: Path, mode: RunMode):
    with Database(tmp_path / f"{mode.value}.sqlite3") as database:
        artifacts = ArtifactStore(tmp_path / f"{mode.value}-artifacts")
        runtime = ToolRuntime(
            database,
            artifacts,
            {"bundled-july": DATA},
            expected_seed_counts={"bundled-july": 81},
        )
        run = runtime.create_run("bundled-july", mode)
        repository = DatabaseRunRepository(database)
        sdk = ScriptedResponses(repository, run.run_id) if mode is RunMode.LIVE else None
        provider = (
            OpenAIResponsesProvider(
                AgentSettings(),
                api_key="test-key",
                client=SimpleNamespace(responses=sdk),
            )
            if sdk is not None
            else DeterministicDemoProvider()
        )
        agent = AgentOrchestrator(
            repository,
            RuntimeToolRegistry(runtime),
            DatabaseEventStore(database),
            provider,
        )
        decision = asyncio.run(agent.execute_run(run.run_id))
        final_run = database.require_run(run.run_id)
        review_case = database.get_review_case_for_run(run.run_id)
        registered = database.list_artifacts(run.run_id)
        events = database.list_events(run.run_id)
        return decision, final_run, review_case, registered, events, sdk


def test_demo_and_responses_adapter_reach_same_verified_bundle(tmp_path: Path) -> None:
    demo = _execute_mode(tmp_path, RunMode.DEMO)
    live = _execute_mode(tmp_path, RunMode.LIVE)
    for decision, run, case, artifacts, events, _sdk in (demo, live):
        assert decision.status == "completed"
        assert run.status is RunState.COMPLETED
        assert run.verification_status.value == "passed"
        assert case is not None and len(case.target_gids) == 20
        assert decision.case_id == case.case_id
        assert {item.name for item in artifacts} >= {
            "nodes_roles.csv",
            "clusters.csv",
            "top_nodes.csv",
        }
        assert any(event.kind is EventKind.COMPLETED for event in events)
        assert all("test-key" not in event.summary for event in events)
    demo_artifacts = {item.name: item.sha256 for item in demo[3]}
    live_artifacts = {item.name: item.sha256 for item in live[3]}
    assert demo_artifacts["nodes_roles.csv"] == live_artifacts["nodes_roles.csv"]
    assert demo_artifacts["clusters.csv"] == live_artifacts["clusters.csv"]
    assert demo_artifacts["top_nodes.csv"] == live_artifacts["top_nodes.csv"]
    assert demo[2].target_gids == live[2].target_gids
    assert len(live[5].requests) == 10
    assert live[5].requests[-1]["text"]["format"]["strict"] is True


def test_agent_resumes_from_persisted_tool_result(tmp_path: Path) -> None:
    database_path = tmp_path / "resume.sqlite3"
    artifact_path = tmp_path / "resume-artifacts"
    with Database(database_path) as database:
        runtime = ToolRuntime(
            database,
            ArtifactStore(artifact_path),
            {"bundled-july": DATA},
            expected_seed_counts={"bundled-july": 81},
        )
        run = runtime.create_run("bundled-july", RunMode.DEMO)
        assert runtime.execute("inspect_dataset", {"run_id": run.run_id})["ok"] is True

    with Database(database_path) as database:
        runtime = ToolRuntime(
            database,
            ArtifactStore(artifact_path),
            {"bundled-july": DATA},
            expected_seed_counts={"bundled-july": 81},
        )
        repository = DatabaseRunRepository(database)
        context = repository.get_compact_context(run.run_id)
        assert context.state is RunState.VALIDATED
        assert context.completed_tools == ("inspect_dataset",)
        agent = AgentOrchestrator(
            repository,
            RuntimeToolRegistry(runtime),
            DatabaseEventStore(database),
            DeterministicDemoProvider(),
        )
        decision = asyncio.run(agent.execute_run(run.run_id))
        assert decision.status == "completed"
        assert database.require_run(run.run_id).verification_status.value == "passed"
