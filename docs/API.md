# Phase 4 HTTP API and integration contract

## Implementation status

The HTTP layer is implemented without importing or replacing Phases 1–3. Its
entry point is `backend.app.main.create_app`. The production analytics, storage,
tools, and orchestrator are owned by those phases and are not implemented here.

The default factory provides `/health`, `/docs`, and `/openapi.json`. Without
injected adapters, `/health` returns HTTP 200 with `backend_ready: false`, while
run/case/query/download/reset operations return `503 BACKEND_NOT_CONFIGURED`.
This is API liveness, not readiness of the complete AML workflow.

`backend/tests/api/fakes.py` contains test doubles only. Neither this module nor
its fabricated assessments are imported by the application or used in demo mode.
The full exit criterion in TODO.md remains pending until real adapters are wired.

## Launch and settings

Run from the repository root after installing `backend/requirements-api.txt`:

```bash
python -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Settings read the repository-root `.env`, with environment variables taking
precedence. Paths in settings are relative to the repository root. API payloads
never accept filesystem paths. `DATABASE_URL` must reference a local SQLite file;
the HTTP layer does not open it. The adapter owns database initialization/recovery.

Important settings from `.env.example`:

| Variable | Default | Effect |
|---|---|---|
| `DEMO_MODE` | `true` | Default mode for new runs and whether demo reset is enabled |
| `OPENAI_API_KEY` | empty | Stored as a secret; required only to execute a live run |
| `OPENAI_MODEL` | `gpt-5-mini` | Passed to the backend when creating a live run |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated exact HTTP origins, no wildcard |
| `API_MAX_ACTIVE_RUNS` | `2` | Maximum concurrent executions in this process (1–8) |
| `API_EXECUTION_TIMEOUT_SECONDS` | `300` | Cooperative execution deadline (up to 900 seconds) |
| `SSE_POLL_SECONDS` | `0.25` | Interval for querying persisted events |
| `SSE_HEARTBEAT_SECONDS` | `15` | Idle connection heartbeat interval |
| `SSE_MAX_STREAMS` | `32` | Maximum concurrent streams in this process (1–256) |

The explicit Uvicorn command controls bind address/port. `BACKEND_HOST` and
`BACKEND_PORT` are validated settings available to future launchers, not implicit
overrides of Uvicorn CLI flags. Use one API process for this MVP; execution limits,
SSE connection accounting, and cancellation coordination are process-local.

`live_configured` in health means only that a non-empty key is configured; it does
not test key validity or external API availability. Health never returns key
values, settings dumps, database paths, or model output.

## Endpoints

Interactive schemas, all query parameters, and complete response fields are in
`/docs` and `/openapi.json`; API DTOs live in `backend/app/api/schemas.py`.

| Method | Path | Response / behavior |
|---|---|---|
| GET | `/health` | 200 liveness/mode/readiness; 503 when an attached backend health check fails |
| POST | `/api/runs` | 201 `RunView` plus `Location`; body `{ "dataset_id": "bundled", "mode": "demo" }` |
| POST | `/api/runs/{run_id}/execute` | 202 execution accepted/already active; 200 for an immutable completed run |
| GET | `/api/runs/{run_id}` | State, executing flag, counts, warnings, case ID, verification, terminal decision |
| GET | `/api/runs/{run_id}/events` | Persisted SSE events; supports replay, follow, and reconnect |
| GET | `/api/runs/{run_id}/nodes` | Paginated and filterable stored assessments |
| GET | `/api/runs/{run_id}/nodes/{gid}` | Node assessment, cluster, and bounded directed ego graph |
| GET | `/api/runs/{run_id}/clusters` | Paginated stored cluster summaries |
| GET | `/api/cases/{case_id}` | Case with ordered immutable target snapshot |
| GET | `/api/runs/{run_id}/artifacts/{name}` | Verified bytes with attachment filename and SHA-256 ETag |
| POST | `/api/demo/reset` | Count of removed demo runs; live state must be preserved |

`dataset_id` defaults to `bundled`; arbitrary uploads and paths are unsupported.
`mode` defaults to settings. Extra request body fields are rejected. Run and case
IDs must be UUIDs. GIDs are canonical non-negative int64 decimal **strings** in
all JSON positions, including graph endpoints, cluster leaders, and case targets.
Numeric JSON GIDs, floats, scientific notation, and leading-zero aliases are rejected.
CSV bytes are served unchanged: conversion from source int64 belongs to Phase 1/2 export.

Creating a run does not execute it. `executing` is separate from the analytical
state from AGENT_LOOP.md, including `clustered` and `exported`. Repeat execute
requests cannot claim the same active/completed run twice. Intermediate committed
states can resume after restart when the adapter recovers stale claims. Terminal
`failed` and `verification_failed` runs require a new run. A missing live key
returns `409 OPENAI_KEY_REQUIRED` before claiming execution and leaves the run
available for a later retry after local configuration is fixed.

## Pagination, sorting, and graph bounds

Nodes and clusters return `{ "items": [...], "total": 25, "offset": 0, "limit": 20 }`.
`offset` defaults to 0 and `limit` to 20; maximum limit is 200. `total` is after
filtering. An offset past the end returns an empty items array.

Node filters combine with AND: `gid` (exact string match), `role`, `cluster_id`,
`is_seed`, `truncated_by_depth`. Nodes sort by persisted `priority_score` descending,
then persisted `role_score` descending, then integer GID ascending without a
floating-point conversion. Clusters sort by `cluster_id` ascending. The query
service never calculates or changes roles or scores.

Ego parameters are `neighbor_hops=1` (1–2), `max_nodes=100` (1–200), and
`max_edges=200` (1–500). Traversal considers both incoming and outgoing neighbors;
returned edges keep their original `src` and `dst`. The center is always included.
Nodes are selected by distance, then numeric GID; induced edges are ordered by
numeric `(src, dst)`. `truncated=true` means a node or edge limit omitted data.
An isolated node returns itself and no edges. The implementation scans stored
batch-sized records in Python; a large-graph adapter should push bounded queries
into storage while retaining these HTTP semantics.

## SSE

Connect before or during execution, or replay after completion:

```bash
curl -N "http://127.0.0.1:8000/api/runs/RUN_ID/events?after=0"
curl -N -H "Last-Event-ID: 12" "http://127.0.0.1:8000/api/runs/RUN_ID/events"
```

Replace `RUN_ID` with the UUID returned by create. `after` is an exclusive
non-negative sequence cursor. `Last-Event-ID` takes precedence when present and
is validated independently. The SSE ID is the run-local sequence, not event UUID.

```text
id: 13
event: verification
data: {"event_id":"...","run_id":"...","sequence":13,"kind":"verification",...}

```

The initial comment includes `retry: 1000`. Heartbeats are SSE comments, not
persisted tool events. `follow=false` drains current events and returns immediately;
default `follow=true` polls until a terminal state or a `needs_user_action` decision.
Terminal streams drain all remaining events, including more than one 100-event
batch, before closing. Clients should stop reconnecting once run status is terminal.
Disconnects free the stream slot without canceling the execution. After headers
are sent, a read failure emits `event: error` with a safe code and closes the stream.
No prompts, hidden reasoning, raw model responses, or stack traces are streamed.

## Artifacts, reset, and errors

Only `nodes_roles.csv`, `clusters.csv`, `top_nodes.csv`, and `audit.json` are valid
artifact names. Downloads require both `status=completed` and
`verification_status=passed`. The API checks returned bytes against the persisted
SHA-256 before responding. The storage adapter must enforce containment beneath
the owning run directory, including symlinks, and never accept arbitrary paths.

Demo reset is disabled when `DEMO_MODE=false`. In demo mode it rejects active
executions or connected event streams. The backend must atomically reject active
demo claims and remove only demo-owned cases, events, and artifacts; live runs
remain intact. This endpoint is intended for the local single-user MVP.

Application errors use `{"error":{"code":"...","message":"..."}}`:

| Status | Typical codes |
|---|---|
| 404 | `RUN_NOT_FOUND`, `NODE_NOT_FOUND`, `CASE_NOT_FOUND`, `ARTIFACT_NOT_FOUND` |
| 409 | `INVALID_STATE`, `OPENAI_KEY_REQUIRED`, `RUN_BUSY`, `ARTIFACT_NOT_VERIFIED`, `ARTIFACT_INTEGRITY_FAILED` |
| 422 | `INVALID_REQUEST`, `INVALID_EVENT_CURSOR` |
| 503 | `BACKEND_NOT_CONFIGURED`, `BACKEND_UNAVAILABLE`, `EXECUTION_CAPACITY`, `STREAM_CAPACITY` |
| 500 | `INTERNAL_ERROR`, `BACKEND_CONTRACT_ERROR` |

Validation errors expose field locations, never offending values. Unhandled
exceptions are contained before reaching the ASGI server logger. Adapter-raised
`AppError` messages must themselves be fixed, safe user-facing text. Capacity
responses include `Retry-After: 1`. Unknown router paths retain FastAPI's default 404.

## Connecting Phases 1–3 later

Implement `RunBackend` and `RunExecutor` from `backend/app/api/ports.py`, then pass
both into the application factory. Neither HTTP routes nor the frontend should
import storage internals or calculate analytical metrics.

```python
# In the future composition root, using real Phase 2/3 adapter implementations:
app = create_app(settings, backend=repository_adapter, executor=orchestrator_adapter)
```

`RunBackend` methods return validated HTTP DTOs. Map the domain/storage models in
the adapter; do not make the domain depend on FastAPI. Read operations and claims
are synchronous and thread-safe; the API executes blocking operations in its
thread pool. `RunExecutor.execute_run` is async and must offload CPU/SQLite work.
It receives only a run UUID and drives the registered tools from Phase 3.

Required integration invariants:

1. `claim_execution` is atomic across repeated requests. Completed runs are
   immutable; failed runs cannot be silently resumed. Recover stale claims on
   application startup without resetting analytical state or tool idempotency.
2. `list_nodes`/`list_clusters` return persisted results, including every orphan
   seed. Return 404 for unknown runs and 409 before assessments are available.
3. `list_events(after, limit)` returns only this run's committed safe DTOs in
   strictly increasing sequence. Commit the final event and terminal status in
   the same transaction. Do not return hidden reasoning or arbitrary payload keys.
4. `execute_run` persists terminal state/decision before returning. Completion
   requires independent verification. On exceptions the API calls `record_failure`
   with `EXECUTION_FAILED`, `EXECUTION_TIMEOUT`, or `EXECUTION_INCOMPLETE`, without
   exception text. `record_failure` must preserve already completed runs.
5. Execution cancellation must cooperate with the orchestrator. Shutdown keeps
   the last committed analytical state and calls `release_execution`; cancellation
   must not leave an analytics thread continuing writes after the claim is released.
6. `read_artifact` returns owned allowlisted bytes and their persisted checksum.
   `reset_demo` enforces its own transaction/claim checks, not just API checks.

After wiring, drive the real workflow through HTTP alone: create, execute, consume
events, read status/nodes/clusters/case, and download all mandatory files. Verify
2,248 assessments, at least 20 ranked targets, all 19 orphan seeds, depth-boundary
uncertainty, unchanged GID digits, exact case snapshot, passed independent checks,
and deterministic CSVs. Then close the Phase 4 integration follow-up in TODO.md.
