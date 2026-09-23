# AML Agent architecture

## 1. Objective

Build one narrow end-to-end product: convert the supplied parquet batch into a verified AML analyst review case. The system must remain useful when the OpenAI API is unavailable; AI coordinates and explains, while deterministic code owns all calculations and validation.

## 2. Architecture decisions

| Decision | Choice | Reason |
|---|---|---|
| Agent topology | One orchestrator with controlled tools | Avoid artificial multi-agent complexity |
| AI integration | OpenAI Responses API | Native iterative function-calling loop |
| Live model | Configurable, default `gpt-5-mini` | Low latency/cost and supports required API features |
| Offline mode | Deterministic provider with the same tool state machine | Reliable judging without an API key |
| Analytics | pandas + NetworkX + NumPy/SciPy | More than sufficient for 2,248 nodes |
| Backend | Python 3.12 + FastAPI + Pydantic | Matches analytical stack and strict schemas |
| Frontend | React + Vite + TypeScript + Cytoscape.js | Fast UI development and focused graph views |
| Storage | SQLite plus filesystem artifacts | Reproducible local state without infrastructure |
| Deployment | Docker Compose | One-command local run |

## 3. Component view

```text
Browser
  React/Vite
  - run controls
  - safe execution trace
  - priority table
  - cluster and ego graph views
  - case before/after state
       |
       | HTTP + Server-Sent Events
       v
FastAPI
  - validates requests
  - serializes GIDs as strings
  - exposes run/case/artifact endpoints
       |
       +--------------------+
       |                    |
       v                    v
Agent orchestrator       Query service
  - state machine          - graph slices
  - allowed tools          - node cards
  - tool budget            - cluster summaries
  - retry policy
       |
       +--------------------+
       |                    |
       v                    v
Provider adapter        Tool registry
  OpenAI Responses        - dataset inspection
  Deterministic demo      - graph analytics
                          - role/rank rules
                          - case/export/verify
                               |
             +-----------------+-----------------+
             v                                   v
        SQLite store                      Artifact store
        runs, events, cases               CSV, JSON audit
```

## 4. Proposed repository layout

```text
backend/
  app/
    api/                  # FastAPI routes and SSE
    agent/                # loop, state machine, prompts, providers
    analytics/            # graph, features, roles, ranking, clusters
    models/               # Pydantic/domain models
    storage/              # SQLite repositories and artifact paths
    tools/                # strict tool registry and implementations
    config.py
    main.py
  tests/
  requirements.txt
frontend/
  src/
    api/
    components/
    pages/
    types/
  package.json
data/
starter/
docs/
```

## 5. Dependency rules

1. `analytics` is pure deterministic Python and has no OpenAI, HTTP, UI, or database dependency.
2. `tools` may call analytics and repositories, but never arbitrary shell commands.
3. `agent` may call only registered tools; it cannot access pandas dataframes, filesystem paths, or SQL directly.
4. `api` calls application services, never NetworkX directly.
5. `frontend` receives GIDs as strings and never performs authoritative score calculations.
6. OpenAI output may select tools and provide cautious wording, but cannot overwrite calculated metrics, roles, scores, or verification status.

## 6. Runtime flow

1. `POST /api/runs` creates a run for the bundled dataset or an uploaded validated bundle.
2. `POST /api/runs/{run_id}/execute` starts the orchestrator.
3. Tool events are persisted before being streamed over `GET /api/runs/{run_id}/events` using SSE.
4. Analytics artifacts are written under `artifacts/{run_id}/` using an atomic temporary-file-then-rename strategy.
5. `create_review_case` persists the top targets and changes the visible before/after state.
6. `verify_run` checks the database state and generated files independently of the agent.
7. Only a verified run can enter `completed` status or expose final downloads.

## 7. API surface

The Phase 4 HTTP layer implements this surface. Its current integration boundary,
settings, request/response semantics, and pending Phase 1–3 wiring are documented
in [API.md](API.md). Until real backend/executor adapters are supplied to
`create_app`, health reports `backend_ready=false` and run operations return 503.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Container health and mode, without secrets |
| `POST` | `/api/runs` | Create a run from a known dataset |
| `POST` | `/api/runs/{run_id}/execute` | Start or safely resume execution |
| `GET` | `/api/runs/{run_id}` | Run status, counts, warnings, final result |
| `GET` | `/api/runs/{run_id}/events` | SSE execution trace |
| `GET` | `/api/runs/{run_id}/nodes` | Paginated/filterable priority list |
| `GET` | `/api/runs/{run_id}/nodes/{gid}` | Node evidence and ego graph |
| `GET` | `/api/runs/{run_id}/clusters` | Cluster summaries |
| `GET` | `/api/cases/{case_id}` | Review case and target snapshot |
| `GET` | `/api/runs/{run_id}/artifacts/{name}` | Whitelisted artifact download |
| `POST` | `/api/demo/reset` | Reset only demo-created state |

## 8. OpenAI boundary

The live provider sends a compact run-state summary and the currently allowed strict function tools to the Responses API. Raw transaction tables and full graph dumps are not sent. Tool outputs return aggregates and evidence needed for the next decision.

The final model output follows a strict `AgentDecision` schema:

```json
{
  "status": "completed",
  "run_id": "uuid",
  "case_id": "uuid",
  "summary": "Review case created and verified.",
  "warnings": ["Depth-4 recipients remain boundary-limited."],
  "recommended_next_step": "Analyst reviews the top 20 targets."
}
```

No hidden reasoning is stored or shown. The audit contains tool calls, sanitized inputs, summaries, warnings, actions, and verification outcomes.

## 9. Reliability policy

- Maximum tool calls per run: 12 by default.
- Maximum attempts per retriable tool: 2.
- OpenAI timeout: 30 seconds, followed by deterministic continuation where safe.
- Tool operations are idempotent by `(run_id, tool_name, ruleset_version)`.
- `create_review_case` uses an idempotency key derived from the run and ranked target snapshot.
- A failed verification marks the run `verification_failed`; it never exposes a success state.
- An invalid model response is rejected by schema validation and retried once before deterministic fallback.

## 10. Security boundary

- `OPENAI_API_KEY` is read only from environment configuration and is never returned by `/health` or logs.
- File tools accept `dataset_id` and `run_id`, not arbitrary paths.
- Artifact downloads are selected from a fixed allowlist.
- No tool executes shell commands or arbitrary Python supplied by the model.
- External actions and account blocking are out of scope.

## 11. Scaling note

At roughly one million nodes, keep the API, tool, and domain contracts but replace in-memory NetworkX computations with an engine such as igraph, graph-tool, Spark GraphFrames, or a graph database/analytical store. Feature tables should become columnar, and graph visualization should remain server-filtered rather than sending the entire network to the browser.
