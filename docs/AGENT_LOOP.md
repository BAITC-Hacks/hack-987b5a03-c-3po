# AML Agent loop

## 1. Purpose

The orchestrator is a bounded state machine, not an open-ended chat. Its job is to select the next valid tool, react to tool results, create one local review case, require verification, and return a structured outcome.

## 2. Inputs to the model

For each turn the provider receives:

1. A stable system instruction describing scope, safety language, and the completion condition.
2. A compact `RunContext` containing `run_id`, current state, mode, completed tools, counts, warnings, remaining tool budget, and whether analyst confirmation is required.
3. Only the function tools allowed in the current persisted state.
4. Previous tool-call items and application-produced `function_call_output` items linked by `call_id`.

Raw parquet content, full graph dumps, secrets, local paths, SQL, and internal exception traces are never sent.

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
  -> completed
```

A tool result changes state only inside its transaction. The model cannot claim or set a state directly.

## 4. Loop pseudocode

```python
async def execute_run(run_id: UUID) -> AgentDecision:
    for step in range(settings.openai_max_tool_calls):
        context = run_repository.get_compact_context(run_id)

        if context.state == RunState.VERIFIED:
            return complete_from_persisted_state(context)

        tools = tool_registry.allowed_definitions(context.state)
        response = await provider.respond(
            context=context,
            tools=tools,
            previous_response_id=context.previous_response_id,
        )

        calls = validate_function_calls(response, tools)
        if not calls:
            raise InvalidAgentResponse("A valid next tool is required")

        for call in calls:
            event_store.tool_started(run_id, call.name, sanitized(call.arguments))
            result = await tool_registry.execute(call.name, call.arguments)
            event_store.tool_completed(run_id, summarize(result))
            provider.add_tool_output(call_id=call.call_id, output=result)

            if not result.ok and not result.error.retriable:
                return fail_from_tool_result(result)

    return fail(run_id, code="TOOL_BUDGET_EXCEEDED")
```

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
