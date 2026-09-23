from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from aml_agent.agent.catalog import (
    TOOL_DEFINITIONS,
    InvalidToolCall,
    allowed_definitions,
    require_allowed_definitions,
    validate_call,
)
from aml_agent.agent.models import (
    AGENT_DECISION_SCHEMA,
    AgentSettings,
    ProviderResponse,
    RunContext,
    RunState,
    ToolCall,
    ToolError,
    ToolResult,
)
from aml_agent.agent.orchestrator import AgentOrchestrator
from aml_agent.agent.providers import (
    DeterministicDemoProvider,
    OpenAIResponsesProvider,
    ProviderTimeout,
    deterministic_decision,
)

RUN_ID = "6ba7b810-9dad-4af6-8640-708e9e8d052f"
CASE_ID = "70071e78-74b5-430d-8a9a-5ef72aeb69f1"
TARGETS = tuple(str(9_007_199_254_740_993 + index) for index in range(20))

NEXT_STATE = {
    "inspect_dataset": RunState.VALIDATED,
    "build_graph": RunState.GRAPH_READY,
    "compute_graph_features": RunState.ANALYZED,
    "cluster_network": RunState.CLUSTERED,
    "assign_roles": RunState.CLASSIFIED,
    "rank_targets": RunState.RANKED,
    "create_review_case": RunState.CASE_CREATED,
    "export_results": RunState.EXPORTED,
    "verify_run": RunState.VERIFIED,
}


class MemoryEventStore:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def record(self, run_id, kind, summary, *, tool_name=None, payload=None) -> None:
        self.events.append(
            {
                "run_id": run_id,
                "sequence": len(self.events) + 1,
                "kind": kind,
                "summary": summary,
                "tool_name": tool_name,
                "payload_json": dict(payload or {}),
            }
        )

    def list_events(self, run_id):
        return [event for event in self.events if event["run_id"] == run_id]


class FakeRepository:
    def __init__(self, mode: str = "demo") -> None:
        self.context = RunContext(run_id=RUN_ID, state=RunState.CREATED, mode=mode)
        self.failures: list[str] = []

    def get_compact_context(self, run_id: str) -> RunContext:
        assert run_id == RUN_ID
        return self.context

    def complete_run(self, run_id: str, decision: object) -> None:
        assert run_id == RUN_ID
        assert self.context.state == RunState.VERIFIED
        assert self.context.verification_status == "passed"
        self.context = replace(self.context, state=RunState.COMPLETED)

    def fail_run(self, run_id: str, error_code: str) -> None:
        assert run_id == RUN_ID
        self.failures.append(error_code)
        state = (
            RunState.VERIFICATION_FAILED if error_code == "VERIFICATION_FAILED" else RunState.FAILED
        )
        self.context = replace(self.context, state=state)


class FakeRegistry:
    """Contract test double. It updates state and records inputs, not analytics."""

    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.calls: list[tuple[str, dict]] = []
        self.scripted_failures: dict[str, list[ToolResult]] = {}
        self.verify_passed = True

    def allowed_definitions(self, state: RunState) -> list[dict]:
        return allowed_definitions(state)

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        if self.scripted_failures.get(name):
            return self.scripted_failures[name].pop(0)
        context = self.repository.context
        assert name in {item["name"] for item in allowed_definitions(context.state)}
        if name == "create_review_case":
            assert tuple(arguments["target_gids"]) == context.target_gids
            assert len(set(arguments["target_gids"])) == 20
        data: dict = {}
        change: dict = {
            "state": NEXT_STATE[name],
            "completed_tools": context.completed_tools + (name,),
        }
        if name == "inspect_dataset":
            data.update(n_nodes=2248, n_edges=3119, n_transactions=4840, n_seed=81)
            change["counts"] = {"n_nodes": 2248, "n_edges": 3119}
        elif name == "rank_targets":
            data.update(limit=20, ranked_count=20, target_gids=list(TARGETS))
            change["target_gids"] = TARGETS
        elif name == "create_review_case":
            data.update(case_id=CASE_ID, target_count=20)
            change["case_id"] = CASE_ID
        elif name == "verify_run":
            data["passed"] = self.verify_passed
            if self.verify_passed:
                change["verification_status"] = "passed"
            else:
                change.update(state=RunState.VERIFICATION_FAILED, verification_status="failed")
        self.repository.context = replace(context, **change)
        return ToolResult(ok=True, tool=name, run_id=RUN_ID, data=data)


class FakeResponses:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.requests: list[dict] = []
        self.demo = DeterministicDemoProvider()

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.requests.append(kwargs)
        if "text" in kwargs:
            decision = deterministic_decision(self.repository.context)
            return SimpleNamespace(
                id=f"response-{len(self.requests)}",
                status="completed",
                output=(),
                output_text=json.dumps(decision.as_dict()),
            )
        context = self.repository.context
        demo = await self.demo.respond(context, allowed_definitions(context.state), None)
        call = demo.calls[0]
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


class InvalidArgumentsOnceResponses(FakeResponses):
    def __init__(self, repository: FakeRepository) -> None:
        super().__init__(repository)
        self.invalid_sent = False

    async def create(self, **kwargs: object) -> SimpleNamespace:
        if not self.invalid_sent and "text" not in kwargs:
            self.invalid_sent = True
            self.requests.append(kwargs)
            return SimpleNamespace(
                id="response-bad",
                status="completed",
                output_text=None,
                output=(
                    SimpleNamespace(
                        type="function_call",
                        call_id="call-bad",
                        name="inspect_dataset",
                        arguments="{bad json",
                    ),
                ),
            )
        return await super().create(**kwargs)


class RefusalResponses:
    async def create(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            id="response-refusal",
            status="completed",
            output_text=None,
            output=(
                SimpleNamespace(
                    type="message",
                    content=(
                        SimpleNamespace(
                            type="refusal",
                            refusal="Sensitive raw refusal text",
                        ),
                    ),
                ),
            ),
        )


class BadTerminalResponses(FakeResponses):
    async def create(self, **kwargs: object) -> SimpleNamespace:
        if "text" in kwargs:
            self.requests.append(kwargs)
            decision = deterministic_decision(self.repository.context).as_dict()
            decision["case_id"] = str(UUID(int=1))
            return SimpleNamespace(
                id="response-bad-terminal",
                status="completed",
                output=(),
                output_text=json.dumps(decision),
            )
        return await super().create(**kwargs)


class MalformedResponses:
    async def create(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(id="malformed", status="completed", output=None, output_text=None)


class SecretErrorResponses:
    async def create(self, **kwargs: object) -> SimpleNamespace:
        raise RuntimeError("sk-local-secret-in-provider-error")


class TimeoutProvider(DeterministicDemoProvider):
    async def respond(self, context, tools, previous_response_id):
        raise ProviderTimeout()


class BadThenGoodProvider(DeterministicDemoProvider):
    def __init__(self) -> None:
        self.bad = True

    async def respond(self, context, tools, previous_response_id):
        if self.bad:
            self.bad = False
            return ProviderResponse(
                response_id="bad-response",
                calls=(ToolCall("bad-call", "inspect_dataset", {"run_id": str(UUID(int=1))}),),
            )
        return await super().respond(context, tools, previous_response_id)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events = MemoryEventStore()

    def make_agent(self, provider=None, *, mode="demo", settings=None):
        repository = FakeRepository(mode)
        registry = FakeRegistry(repository)
        provider = provider or DeterministicDemoProvider()
        agent = AgentOrchestrator(repository, registry, self.events, provider, settings=settings)
        return agent, repository, registry

    async def test_demo_executes_all_transitions_and_verifies(self) -> None:
        agent, repository, registry = self.make_agent()
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "completed")
        self.assertEqual(decision.case_id, CASE_ID)
        self.assertEqual(repository.context.state, RunState.COMPLETED)
        self.assertEqual([name for name, _ in registry.calls], list(NEXT_STATE))
        self.assertEqual(tuple(registry.calls[6][1]["target_gids"]), TARGETS)
        events = self.events.list_events(RUN_ID)
        self.assertEqual([event["sequence"] for event in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[-1]["kind"], "completed")
        self.assertEqual([event["kind"] for event in events].count("verification"), 1)
        self.assertTrue(all("arguments" not in event["payload_json"] for event in events))

    async def test_live_adapter_uses_the_same_tools_and_structured_terminal(self) -> None:
        demo_agent, demo_repository, demo_registry = self.make_agent()
        demo_result = await demo_agent.execute_run(RUN_ID)
        live_repository = FakeRepository("live")
        live_registry = FakeRegistry(live_repository)
        sdk = FakeResponses(live_repository)
        provider = OpenAIResponsesProvider(
            AgentSettings(), api_key="test-key", client=SimpleNamespace(responses=sdk)
        )
        agent = AgentOrchestrator(live_repository, live_registry, self.events, provider)
        live_result = await agent.execute_run(RUN_ID)
        self.assertEqual(live_result.status, "completed")
        self.assertEqual(live_result.case_id, demo_result.case_id)
        self.assertEqual(live_repository.context.verification_status, "passed")
        self.assertEqual(live_registry.calls, demo_registry.calls)
        self.assertEqual(len(sdk.requests), 10)
        self.assertEqual(sdk.requests[0]["tool_choice"], "required")
        self.assertFalse(sdk.requests[0]["parallel_tool_calls"])
        self.assertTrue(all(tool["strict"] for tool in sdk.requests[0]["tools"]))
        second_input = sdk.requests[1]["input"]
        self.assertEqual(second_input[0]["type"], "function_call_output")
        self.assertEqual(second_input[0]["call_id"], "call-1")
        self.assertEqual(sdk.requests[1]["previous_response_id"], "response-1")
        self.assertEqual(sdk.requests[-1]["text"]["format"]["type"], "json_schema")
        self.assertTrue(sdk.requests[-1]["text"]["format"]["strict"])
        self.assertEqual(sdk.requests[-1]["input"][0]["call_id"], "call-9")

    async def test_missing_live_key_requires_action_without_key_leak(self) -> None:
        provider = OpenAIResponsesProvider(AgentSettings(), api_key="", client=SimpleNamespace())
        agent, repository, registry = self.make_agent(provider, mode="live")
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "needs_user_action")
        self.assertEqual(repository.context.state, RunState.CREATED)
        self.assertEqual(registry.calls, [])
        self.assertNotIn("test-key", str(self.events.list_events(RUN_ID)))

    async def test_resumed_ranked_run_uses_persisted_snapshot(self) -> None:
        repository = FakeRepository("live")
        repository.context = replace(repository.context, state=RunState.RANKED, target_gids=TARGETS)
        registry = FakeRegistry(repository)
        sdk = FakeResponses(repository)
        provider = OpenAIResponsesProvider(
            AgentSettings(), api_key="test-key", client=SimpleNamespace(responses=sdk)
        )
        agent = AgentOrchestrator(repository, registry, self.events, provider)
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "completed")
        self.assertEqual(
            [name for name, _ in registry.calls],
            [
                "create_review_case",
                "export_results",
                "verify_run",
            ],
        )
        context_message = json.loads(sdk.requests[0]["input"][-1]["content"])
        self.assertEqual(context_message["run_context"]["target_gids"], list(TARGETS))
        self.assertNotIn("previous_response_id", sdk.requests[0])

    async def test_required_analyst_confirmation_pauses_before_case_creation(self) -> None:
        agent, repository, registry = self.make_agent()
        repository.context = replace(
            repository.context,
            state=RunState.RANKED,
            target_gids=TARGETS,
            analyst_confirmation_required=True,
        )
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "needs_user_action")
        self.assertEqual(repository.context.state, RunState.RANKED)
        self.assertEqual(registry.calls, [])

    async def test_openai_invalid_arguments_get_linked_error_then_correction(self) -> None:
        repository = FakeRepository("live")
        registry = FakeRegistry(repository)
        sdk = InvalidArgumentsOnceResponses(repository)
        provider = OpenAIResponsesProvider(
            AgentSettings(), api_key="test-key", client=SimpleNamespace(responses=sdk)
        )
        decision = await AgentOrchestrator(repository, registry, self.events, provider).execute_run(
            RUN_ID
        )
        self.assertEqual(decision.status, "completed")
        self.assertEqual(len(registry.calls), 9)
        corrective = sdk.requests[1]
        self.assertEqual(corrective["previous_response_id"], "response-bad")
        self.assertEqual(corrective["input"][0]["call_id"], "call-bad")
        self.assertEqual(
            json.loads(corrective["input"][0]["output"])["error"]["code"], "INVALID_ARGUMENT"
        )

    async def test_openai_refusal_requires_action_without_raw_refusal(self) -> None:
        provider = OpenAIResponsesProvider(
            AgentSettings(),
            api_key="test-key",
            client=SimpleNamespace(responses=RefusalResponses()),
        )
        agent, repository, registry = self.make_agent(provider, mode="live")
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "needs_user_action")
        self.assertEqual(repository.context.state, RunState.CREATED)
        self.assertEqual(registry.calls, [])
        self.assertNotIn("Sensitive raw refusal text", str(self.events.list_events(RUN_ID)))

    async def test_malformed_sdk_response_falls_back_without_executing_model_call(self) -> None:
        provider = OpenAIResponsesProvider(
            AgentSettings(),
            api_key="test-key",
            client=SimpleNamespace(responses=MalformedResponses()),
        )
        agent, _, registry = self.make_agent(provider, mode="live")
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "completed")
        self.assertEqual([name for name, _ in registry.calls], list(NEXT_STATE))

    async def test_provider_exception_body_is_not_persisted(self) -> None:
        provider = OpenAIResponsesProvider(
            AgentSettings(),
            api_key="test-key",
            client=SimpleNamespace(responses=SecretErrorResponses()),
        )
        agent, _, registry = self.make_agent(provider, mode="live")
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "failed")
        self.assertEqual(registry.calls, [])
        self.assertNotIn("sk-local-secret", str(self.events.list_events(RUN_ID)))

    async def test_false_terminal_model_decision_uses_verified_local_result(self) -> None:
        repository = FakeRepository("live")
        registry = FakeRegistry(repository)
        sdk = BadTerminalResponses(repository)
        provider = OpenAIResponsesProvider(
            AgentSettings(), api_key="test-key", client=SimpleNamespace(responses=sdk)
        )
        decision = await AgentOrchestrator(repository, registry, self.events, provider).execute_run(
            RUN_ID
        )
        self.assertEqual(decision.status, "completed")
        self.assertEqual(decision.case_id, CASE_ID)
        self.assertIn(
            "Used verified local decision",
            [event["summary"] for event in self.events.list_events(RUN_ID)],
        )

    async def test_invalid_call_is_rejected_before_execution_then_corrected(self) -> None:
        agent, _, registry = self.make_agent(BadThenGoodProvider())
        result = await agent.execute_run(RUN_ID)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(registry.calls), 9)
        self.assertEqual(registry.calls[0][0], "inspect_dataset")
        self.assertIn(
            "Invalid tool call; requesting correction",
            [event["summary"] for event in self.events.list_events(RUN_ID)],
        )

    async def test_model_timeout_retries_then_uses_demo_transition(self) -> None:
        agent, repository, registry = self.make_agent(TimeoutProvider())
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "completed")
        self.assertEqual(len(registry.calls), 9)
        self.assertEqual(repository.context.verification_status, "passed")
        self.assertIn(
            "Model timed out; using safe transition",
            [event["summary"] for event in self.events.list_events(RUN_ID)],
        )

    async def test_retriable_tool_failure_is_retried_once(self) -> None:
        agent, _, registry = self.make_agent()
        registry.scripted_failures["build_graph"] = [
            ToolResult(
                ok=False,
                tool="build_graph",
                run_id=RUN_ID,
                error=ToolError("ANALYTICS_FAILED", retriable=True),
            )
        ]
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "completed")
        self.assertEqual([name for name, _ in registry.calls].count("build_graph"), 2)
        self.assertEqual(len(registry.calls), 10)

    async def test_nonretriable_tool_failure_stops_run(self) -> None:
        agent, repository, registry = self.make_agent()
        registry.scripted_failures["inspect_dataset"] = [
            ToolResult(
                ok=False,
                tool="inspect_dataset",
                run_id=RUN_ID,
                error=ToolError("DATASET_INCONSISTENT", retriable=False),
            )
        ]
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "failed")
        self.assertEqual(repository.context.state, RunState.FAILED)
        self.assertEqual(len(registry.calls), 1)

    async def test_tool_budget_fails_before_extra_execution(self) -> None:
        agent, repository, registry = self.make_agent(settings=AgentSettings(max_tool_calls=2))
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "failed")
        self.assertEqual(repository.failures[-1], "TOOL_BUDGET_EXCEEDED")
        self.assertEqual(len(registry.calls), 2)

    async def test_failed_verification_never_completes(self) -> None:
        agent, repository, registry = self.make_agent()
        registry.verify_passed = False
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "failed")
        self.assertEqual(repository.context.state, RunState.VERIFICATION_FAILED)
        self.assertNotIn("completed", [event["kind"] for event in self.events.list_events(RUN_ID)])

    async def test_tool_exception_text_is_not_persisted(self) -> None:
        agent, _, registry = self.make_agent()
        secret = "SECRET-EXCEPTION-BODY"

        async def explode(name, arguments):
            raise RuntimeError(secret)

        registry.execute = explode
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "failed")
        self.assertNotIn(secret, str(self.events.list_events(RUN_ID)))

    async def test_tool_cannot_skip_a_state(self) -> None:
        agent, repository, registry = self.make_agent()
        original = registry.execute

        async def jump(name, arguments):
            result = await original(name, arguments)
            if name == "inspect_dataset":
                repository.context = replace(repository.context, state=RunState.EXPORTED)
            return result

        registry.execute = jump
        decision = await agent.execute_run(RUN_ID)
        self.assertEqual(decision.status, "failed")
        self.assertEqual(repository.failures[-1], "INVALID_STATE")
        self.assertEqual(len(registry.calls), 1)


class CatalogTests(unittest.TestCase):
    def test_terminal_schema_matches_documented_contract(self) -> None:
        document = (Path(__file__).resolve().parents[2] / "docs" / "AGENT_LOOP.md").read_text()
        block = (
            document.split("## 7.", 1)[1]
            .split("```json", 1)[1]
            .split("```", 1)[0]
        )
        self.assertEqual(json.loads(block), AGENT_DECISION_SCHEMA)

    def test_published_schemas_match_code(self) -> None:
        document = (Path(__file__).resolve().parents[2] / "docs" / "TOOLS.md").read_text()
        block = (
            document.split("## 3.", 1)[1]
            .split("```json", 1)[1]
            .split("```", 1)[0]
        )
        published = json.loads(block)
        self.assertEqual({item["name"]: item for item in published}, TOOL_DEFINITIONS)
        for definition in published:
            self.assertTrue(definition["strict"])
            self.assertFalse(definition["parameters"]["additionalProperties"])

    def test_ranked_case_requires_exact_unique_snapshot(self) -> None:
        context = RunContext(run_id=RUN_ID, state=RunState.RANKED, mode="demo", target_gids=TARGETS)
        definitions = allowed_definitions(RunState.RANKED)
        require_allowed_definitions(RunState.RANKED, definitions)
        call = ToolCall(
            "one",
            "create_review_case",
            {
                "run_id": RUN_ID,
                "title": "Transfer network for review",
                "target_gids": list(TARGETS[:-1]) + [TARGETS[0]],
            },
        )
        with self.assertRaises(InvalidToolCall):
            validate_call(call, context, definitions)

    def test_wrong_state_and_arbitrary_field_are_rejected(self) -> None:
        context = RunContext(run_id=RUN_ID, state=RunState.CREATED, mode="demo")
        definitions = allowed_definitions(RunState.CREATED)
        with self.assertRaises(InvalidToolCall):
            validate_call(ToolCall("one", "build_graph", {"run_id": RUN_ID}), context, definitions)
        with self.assertRaises(InvalidToolCall):
            validate_call(
                ToolCall("one", "inspect_dataset", {"run_id": RUN_ID, "path": "/tmp"}),
                context,
                definitions,
            )
        with self.assertRaises(InvalidToolCall):
            validate_call(
                ToolCall(
                    "one", "inspect_dataset", f'{{"run_id":"{RUN_ID}","run_id":"{RUN_ID}"}}'
                ),
                context,
                definitions,
            )

    def test_gid_int64_and_finite_number_rules(self) -> None:
        context = RunContext(run_id=RUN_ID, state=RunState.CLASSIFIED, mode="demo")
        definitions = allowed_definitions(RunState.CLASSIFIED)
        with self.assertRaises(InvalidToolCall):
            validate_call(
                ToolCall(
                    "one",
                    "get_node_evidence",
                    {
                        "run_id": RUN_ID,
                        "gid": str(2**63),
                        "neighbor_hops": 1,
                    },
                ),
                context,
                definitions,
            )
        context = replace(context, state=RunState.ANALYZED)
        definitions = allowed_definitions(RunState.ANALYZED)
        with self.assertRaises(InvalidToolCall):
            validate_call(
                ToolCall(
                    "one",
                    "cluster_network",
                    {
                        "run_id": RUN_ID,
                        "resolution": float("nan"),
                        "random_seed": 42,
                    },
                ),
                context,
                definitions,
            )

    def test_settings_read_documented_environment_names(self) -> None:
        settings = AgentSettings.from_env(
            {
                "OPENAI_MODEL": "configured-model",
                "OPENAI_TIMEOUT_SECONDS": "15",
                "OPENAI_MAX_TOOL_CALLS": "11",
            }
        )
        self.assertEqual(
            (settings.model, settings.timeout_seconds, settings.max_tool_calls),
            ("configured-model", 15.0, 11),
        )
        with self.assertRaises(ValueError):
            AgentSettings.from_env({"OPENAI_TIMEOUT_SECONDS": "nan"})
