"""Provider-neutral, state-bounded execution of registered AML tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol
from uuid import UUID

from .catalog import (
    NEXT_STATE_BY_TOOL,
    NEXT_WRITE_TOOL,
    READ_ONLY_TOOLS,
    InvalidToolCall,
    require_allowed_definitions,
    validate_call,
)
from .events import AgentEventStore
from .models import (
    AgentDecision,
    AgentSettings,
    ProviderResponse,
    RunContext,
    RunState,
    ToolCall,
    ToolError,
    ToolResult,
)
from .providers import (
    AgentProvider,
    DeterministicDemoProvider,
    MissingAPIKey,
    ProviderInvalidResponse,
    ProviderRefusal,
    ProviderTimeout,
    ProviderUnavailable,
    deterministic_decision,
)


class RunRepository(Protocol):
    def get_compact_context(self, run_id: str) -> RunContext: ...

    def complete_run(self, run_id: str, decision: AgentDecision) -> None: ...

    def fail_run(self, run_id: str, error_code: str) -> None: ...


class AgentToolRegistry(Protocol):
    def allowed_definitions(self, state: RunState) -> list[dict[str, Any]]: ...

    async def execute(
        self, name: str, arguments: Mapping[str, Any]
    ) -> ToolResult | Mapping[str, Any]: ...


class AgentOrchestrator:
    def __init__(
        self,
        repository: RunRepository,
        registry: AgentToolRegistry,
        events: AgentEventStore,
        provider: AgentProvider,
        *,
        settings: AgentSettings | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.events = events
        self.provider = provider
        self.settings = settings or AgentSettings()
        self.demo_provider = DeterministicDemoProvider()

    def _event(
        self,
        run_id: str,
        kind: str,
        summary: str,
        *,
        tool_name: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.events.record(run_id, kind, summary, tool_name=tool_name, payload=payload)

    def _fail(self, context: RunContext, code: str) -> AgentDecision:
        if context.state not in (RunState.FAILED, RunState.VERIFICATION_FAILED, RunState.COMPLETED):
            self.repository.fail_run(context.run_id, code)
        self._event(context.run_id, "failed", f"Run stopped: {code}", payload={"error_code": code})
        return AgentDecision(
            status="failed",
            run_id=context.run_id,
            case_id=context.case_id,
            summary=f"Run stopped: {code}.",
            warnings=tuple(context.warnings[:10]),
            recommended_next_step="Review the run events and resolve the reported error.",
        )

    def _needs_user_action(self, context: RunContext, code: str) -> AgentDecision:
        self._event(
            context.run_id, "warning", f"Run needs input: {code}", payload={"error_code": code}
        )
        if code == "OPENAI_API_KEY_REQUIRED":
            next_step = "Set OPENAI_API_KEY in the server environment, then resume the run."
        elif code == "ANALYST_CONFIRMATION_REQUIRED":
            next_step = "Obtain analyst confirmation, then resume the run."
        else:
            next_step = "Review the provider configuration, then resume the run."
        return AgentDecision(
            status="needs_user_action",
            run_id=context.run_id,
            case_id=context.case_id,
            summary=f"Run paused: {code}.",
            warnings=tuple(context.warnings[:10]),
            recommended_next_step=next_step,
        )

    @staticmethod
    def _validate_batch(
        response: ProviderResponse,
        context: RunContext,
        exposed: list[dict[str, Any]],
    ) -> list[tuple[ToolCall, dict[str, Any]]]:
        calls = response.calls
        if (
            not isinstance(calls, (tuple, list))
            or not calls
            or len(calls) > 3
            or any(not isinstance(call, ToolCall) for call in calls)
            or any(not isinstance(call.call_id, str) for call in calls)
            or any(not isinstance(call.name, str) for call in calls)
            or len({call.call_id for call in calls}) != len(calls)
        ):
            raise InvalidToolCall("Expected one transition or up to three evidence calls")
        if len(calls) > 1 and any(call.name not in READ_ONLY_TOOLS for call in calls):
            raise InvalidToolCall("Only evidence calls may be batched")
        return [(call, validate_call(call, context, exposed)) for call in calls]

    async def _execute_tool(
        self,
        context: RunContext,
        call: ToolCall,
        arguments: dict[str, Any],
        used_calls: int,
    ) -> tuple[ToolResult | None, int]:
        for attempt in range(1, self.settings.max_tool_attempts + 1):
            if used_calls >= self.settings.max_tool_calls:
                return None, used_calls
            used_calls += 1
            self._event(
                context.run_id,
                "tool_started",
                f"{call.name} started",
                tool_name=call.name,
                payload={"attempt": attempt},
            )
            try:
                raw_result = await self.registry.execute(call.name, arguments)
                result = ToolResult.from_value(raw_result)
                if result.tool != call.name or result.run_id != context.run_id:
                    raise ValueError("Tool result does not match the call")
            except Exception:
                # A storage/analytics exception may contain paths or secrets; keep it internal.
                result = ToolResult(
                    ok=False,
                    tool=call.name,
                    run_id=context.run_id,
                    error=ToolError(code="INTERNAL_ERROR", retriable=True),
                )
            if result.ok:
                self._event(
                    context.run_id,
                    "tool_completed",
                    f"{call.name} completed",
                    tool_name=call.name,
                    payload={"attempt": attempt},
                )
                return result, used_calls
            assert result.error is not None
            self._event(
                context.run_id,
                "tool_completed",
                f"{call.name} failed: {result.error.code}",
                tool_name=call.name,
                payload={"attempt": attempt, "error_code": result.error.code},
            )
            if not result.error.retriable or attempt == self.settings.max_tool_attempts:
                return result, used_calls
            self._event(
                context.run_id,
                "warning",
                f"Retrying {call.name}",
                tool_name=call.name,
                payload={"attempt": attempt + 1},
            )
        raise AssertionError("Unreachable retry loop")

    async def _finish(self, context: RunContext, previous_response_id: str | None) -> AgentDecision:
        if context.verification_status != "passed" or not context.case_id:
            return self._fail(context, "VERIFICATION_FAILED")
        try:
            decision = await self.provider.final_decision(context, previous_response_id)
            if (
                decision.status != "completed"
                or decision.run_id != context.run_id
                or decision.case_id != context.case_id
            ):
                raise ProviderInvalidResponse()
            # Warnings in the final result remain authoritative repository facts.
            decision = replace(decision, warnings=tuple(context.warnings[:10]))
        except Exception:
            self._event(context.run_id, "warning", "Used verified local decision")
            decision = deterministic_decision(context)
        try:
            self.repository.complete_run(context.run_id, decision)
        except Exception:
            return self._fail(context, "INTERNAL_ERROR")
        completed = self.repository.get_compact_context(context.run_id)
        if completed.state != RunState.COMPLETED or completed.verification_status != "passed":
            return self._fail(context, "VERIFICATION_FAILED")
        self._event(context.run_id, "completed", "Run completed after passed verification")
        return decision

    async def execute_run(self, run_id: str) -> AgentDecision:
        UUID(run_id)
        context = self.repository.get_compact_context(run_id)
        if (
            context.state == RunState.COMPLETED
            and context.verification_status == "passed"
            and context.case_id
        ):
            return AgentDecision(
                status="completed",
                run_id=run_id,
                case_id=context.case_id,
                summary="Review case and verified exports are ready.",
                warnings=tuple(context.warnings[:10]),
                recommended_next_step="Analyst reviews the ranked targets and their evidence.",
            )
        if context.state in (RunState.FAILED, RunState.VERIFICATION_FAILED):
            return AgentDecision(
                status="failed",
                run_id=run_id,
                case_id=context.case_id,
                summary="Run is in a failed state.",
                warnings=tuple(context.warnings[:10]),
                recommended_next_step="Review the run events before retrying.",
            )
        self._event(run_id, "started", "Agent started", payload={"state": context.state.value})
        used_calls = 0
        previous_response_id: str | None = None
        invalid_responses = 0
        timeouts = 0
        evidence_calls = 0

        while True:
            context = self.repository.get_compact_context(run_id)
            context = replace(
                context,
                remaining_tool_budget=self.settings.max_tool_calls - used_calls,
            )
            if context.state in (RunState.VERIFIED, RunState.COMPLETED):
                return await self._finish(context, previous_response_id)
            if context.state in (RunState.FAILED, RunState.VERIFICATION_FAILED):
                return self._fail(context, "INVALID_STATE")
            if context.state == RunState.RANKED and context.analyst_confirmation_required:
                return self._needs_user_action(context, "ANALYST_CONFIRMATION_REQUIRED")
            if used_calls >= self.settings.max_tool_calls:
                return self._fail(context, "TOOL_BUDGET_EXCEEDED")
            try:
                exposed = self.registry.allowed_definitions(context.state)
                require_allowed_definitions(context.state, exposed)
            except Exception:
                return self._fail(context, "INVALID_STATE")

            output_provider: AgentProvider = self.provider
            try:
                response = await self.provider.respond(context, exposed, previous_response_id)
                if not isinstance(response, ProviderResponse):
                    raise ProviderInvalidResponse()
                previous_response_id = response.response_id
                timeouts = 0
            except MissingAPIKey:
                return self._needs_user_action(context, "OPENAI_API_KEY_REQUIRED")
            except ProviderRefusal:
                return self._needs_user_action(context, "MODEL_REFUSAL")
            except ProviderTimeout:
                if timeouts < self.settings.max_timeout_retries:
                    timeouts += 1
                    self._event(run_id, "warning", "Model timed out; retrying")
                    continue
                self._event(run_id, "warning", "Model timed out; using safe transition")
                try:
                    response = await self._fallback(context, exposed)
                except ProviderInvalidResponse:
                    return self._fail(context, "INVALID_ARGUMENT")
                output_provider = self.demo_provider
                previous_response_id = None
                timeouts = 0
            except ProviderInvalidResponse:
                if invalid_responses < self.settings.max_invalid_responses:
                    invalid_responses += 1
                    self.provider.reject_response(ProviderResponse(previous_response_id, ()))
                    self._event(run_id, "warning", "Invalid model response; requesting correction")
                    continue
                self._event(run_id, "warning", "Invalid model response; using safe transition")
                try:
                    response = await self._fallback(context, exposed)
                except ProviderInvalidResponse:
                    return self._fail(context, "INVALID_ARGUMENT")
                output_provider = self.demo_provider
                previous_response_id = None
            except ProviderUnavailable:
                return self._fail(context, "MODEL_UNAVAILABLE")
            except Exception:
                return self._fail(context, "MODEL_UNAVAILABLE")

            try:
                batch = self._validate_batch(response, context, exposed)
                if evidence_calls + sum(call.name in READ_ONLY_TOOLS for call, _ in batch) > 3:
                    raise InvalidToolCall("Evidence-call limit exceeded")
            except InvalidToolCall:
                if (
                    output_provider is self.provider
                    and invalid_responses < self.settings.max_invalid_responses
                ):
                    invalid_responses += 1
                    self.provider.reject_response(response)
                    self._event(run_id, "warning", "Invalid tool call; requesting correction")
                    continue
                self._event(run_id, "warning", "Invalid tool call; using safe transition")
                try:
                    response = await self._fallback(context, exposed)
                except ProviderInvalidResponse:
                    return self._fail(context, "INVALID_ARGUMENT")
                output_provider = self.demo_provider
                previous_response_id = None
                try:
                    batch = self._validate_batch(response, context, exposed)
                except InvalidToolCall:
                    return self._fail(context, "INVALID_ARGUMENT")

            invalid_responses = 0
            for call, arguments in batch:
                result, used_calls = await self._execute_tool(context, call, arguments, used_calls)
                if result is None:
                    return self._fail(context, "TOOL_BUDGET_EXCEEDED")
                output_provider.add_tool_output(call.call_id, result)
                if not result.ok:
                    assert result.error is not None
                    return self._fail(context, result.error.code)
                updated = self.repository.get_compact_context(run_id)
                if call.name in READ_ONLY_TOOLS:
                    evidence_calls += 1
                    if updated.state != context.state:
                        return self._fail(updated, "INVALID_STATE")
                elif not (
                    updated.state == NEXT_STATE_BY_TOOL[call.name]
                    or (call.name == "verify_run" and updated.state == RunState.COMPLETED)
                ):
                    return self._fail(updated, "INVALID_STATE")
                if call.name == "create_review_case":
                    if updated.state != RunState.CASE_CREATED or not updated.case_id:
                        return self._fail(updated, "CASE_WRITE_FAILED")
                    self._event(run_id, "action", "Local review case created", tool_name=call.name)
                if call.name == "verify_run":
                    passed = result.data.get("passed") is True
                    self._event(
                        run_id,
                        "verification",
                        "Verification passed" if passed else "Verification failed",
                        tool_name=call.name,
                        payload={"passed": passed},
                    )
                    if (
                        not passed
                        or updated.state not in (RunState.VERIFIED, RunState.COMPLETED)
                        or updated.verification_status != "passed"
                    ):
                        return self._fail(updated, "VERIFICATION_FAILED")

    async def _fallback(
        self,
        context: RunContext,
        exposed: list[dict[str, Any]],
    ) -> ProviderResponse:
        if NEXT_WRITE_TOOL.get(context.state) not in {item["name"] for item in exposed}:
            raise ProviderInvalidResponse()
        self.provider.reset()
        return await self.demo_provider.respond(context, exposed, None)
