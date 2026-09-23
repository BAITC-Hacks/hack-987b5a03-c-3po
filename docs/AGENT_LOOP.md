# AML Agent loop

## 1. Purpose

The orchestrator is a bounded state machine, not an open-ended chat. Its job is to select the next valid tool, react to tool results, create one local review case, require verification, and return a structured outcome.

## 2. Inputs to the model

For each turn the provider receives:

1. A stable system instruction describing scope, safety language, and the completion condition.
2. A compact `RunContext` containing `run_id`, current state, mode, completed tools, counts, warnings, remaining tool budget, and whether analyst confirmation is required.
3. Only the function tools allowed in the current persisted state.
4. Previous tool-call items and application-produced `function_call_output` items linked by `call_id` during the active execution.

Raw parquet content, full graph dumps, secrets, local paths, SQL, and internal exception traces are never sent.
The Responses chain is held only during an active execution. A resumed run starts a new chain from its persisted state and ranked snapshot, so no model transcript needs to be stored.

## 3. State machine

```text
created
  -> inspect_dataset
validated
  -> build_graph
graph_ready
  -> compute_graph_features
analyzed
  -> cluster_network
clustered
  -> assign_roles
classified
  -> rank_targets
ranked
  -> optional get_node_evidence for selected targets
  -> create_review_case
case_created
  -> export_results
exported
  -> verify_run
verified
  -> completed (the tool runtime records terminal audit events after a passed verification)
```

A tool result changes state only inside its transaction. The model cannot claim or set a state directly. In the production runtime, a successful `verify_run` records verification and completion events and immediately completes the run. The orchestrator then validates the terminal decision against the persisted passed verification and case.
If `analyst_confirmation_required` is true at `ranked`, execution returns `needs_user_action` before creating a case.

## 4. Loop pseudocode

```python
async def execute_run(run_id: UUID) -> AgentDecision:
    used_tool_calls = 0
    previous_response_id = None
    while True:
        context = run_repository.get_compact_context(run_id)

        if context.state in (RunState.VERIFIED, RunState.COMPLETED):
            decision = await provider.final_decision(context, previous_response_id)
            return complete_only_if_verification_passed(context, decision)

        if used_tool_calls >= settings.openai_max_tool_calls:
            return fail(run_id, code="TOOL_BUDGET_EXCEEDED")

        tools = tool_registry.allowed_definitions(context.state)
        response = await provider.respond(
            context=context,
            tools=tools,
            previous_response_id=previous_response_id,
        )
        previous_response_id = response.response_id

        calls = validate_function_calls(response, tools)
        if not calls:
            raise InvalidAgentResponse("A valid next tool is required")

        for call in calls:
            result = await tool_registry.execute(call.name, call.arguments)
            used_tool_calls += 1
            # ToolRuntime records tool and verification events in SQLite.
            provider.add_tool_output(call_id=call.call_id, output=safe_summary(result))

            if not result.ok and not result.error.retriable:
                return fail_from_tool_result(result)

```

The terminal decision uses Structured Outputs. If that response is invalid, refused, or times out after verification, the orchestrator uses a fixed local decision derived from persisted state. Model wording cannot change the case snapshot, checks, or completion gate.

The implementation may execute only one state-changing tool per loop iteration. Read-only `get_node_evidence` calls can be batched up to three targets.

## 5. System instruction contract

The production system instruction must enforce these points:

- You are AML Agent, an investigation workflow orchestrator.
- Use only available function tools and obey their current-state descriptions.
- Never infer guilt, identity, occupation, income, or attributes absent from the dataset.
- Never calculate or alter graph metrics yourself.
- Describe findings as structural indicators or review hypotheses.
- Treat depth-4 missing outflow and seed inflow as explicit uncertainty.
- Create exactly one local review case from the persisted ranking.
- Select the case title only from the server-approved cautious allowlist; never invent a guilt or criminal label.
- A run is complete only when `verify_run` returns `passed: true`.
- Do not reveal hidden reasoning. Produce short decisions and safe tool summaries only.

## 6. Provider interface

```python
class AgentProvider(Protocol):
    async def respond(
        self,
        context: RunContext,
        tools: list[dict],
        previous_response_id: str | None,
    ) -> ProviderResponse: ...
```

### OpenAIResponsesProvider

- Uses the official OpenAI Python SDK and Responses API.
- Reads model and timeout from settings.
- Sends strict function definitions from the registry.
- Returns normalized calls containing `call_id`, tool name, and validated arguments.
- Uses Structured Outputs for the terminal `AgentDecision`.

### DeterministicDemoProvider

- Uses the same persisted state and same tool registry.
- Selects the only valid next state-changing tool from a static policy.
- Uses deterministic templates for titles, warnings, and summaries.
- Does not fake analytics, case writes, exports, events, or verification.

## 7. Terminal decision schema

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string",
      "enum": ["completed", "failed", "needs_user_action"]
    },
    "run_id": {"type": "string", "format": "uuid"},
    "case_id": {
      "type": ["string", "null"],
      "format": "uuid"
    },
    "summary": {"type": "string", "maxLength": 500},
    "warnings": {
      "type": "array",
      "items": {"type": "string", "maxLength": 240},
      "maxItems": 10
    },
    "recommended_next_step": {"type": "string", "maxLength": 300}
  },
  "required": [
    "status",
    "run_id",
    "case_id",
    "summary",
    "warnings",
    "recommended_next_step"
  ],
  "additionalProperties": false
}
```

`case_id` is non-null only after a case is persisted. `completed` is accepted only when repository verification status is `passed`, regardless of model text.

## 8. Error and retry policy

| Failure | Behavior |
|---|---|
| OpenAI timeout | Retry once, then continue with deterministic provider if the current transition is unambiguous |
| Missing API key in live mode | Return `needs_user_action`; never log the environment value |
| Invalid model arguments | Reject before tool execution and request one corrected call |
| Non-retriable data error | Stop run and expose actionable validation evidence |
| Retriable analytics/storage error | Retry tool once using the same idempotency key |
| Case already exists | Return existing case with `idempotent_replay: true` |
| Verification failure | Persist failed checks; do not mark run complete |
| Tool budget exhausted | Fail safely and retain all completed artifacts for diagnosis |

## 9. UI trace policy

Allowed trace content:

```text
Agent started
-> inspect_dataset()
OK 2,248 nodes; edges and transactions reconcile
-> build_graph()
OK 444 depth-boundary nodes flagged
-> assign_roles()
OK 2,248 of 2,248 nodes classified
-> create_review_case()
OK case AML-2026-07-001 created with 20 targets
-> verify_run()
OK all mandatory checks passed
```

Do not expose prompts, hidden reasoning, token-level thoughts, stack traces, secrets, or unbounded raw tool output.

## 10. Golden-path completion criteria

The loop succeeds only when:

- all 2,248 nodes have a valid role, cluster, score, and evidence;
- at least 20 targets are ranked;
- a review case contains the exact ranked snapshot;
- all required files exist with valid hashes and schemas;
- `verify_run` passes;
- the UI receives a completed event and shows the after-state.
