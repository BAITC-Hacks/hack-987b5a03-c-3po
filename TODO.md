# AML Agent implementation plan

This is the ordered hackathon checklist. Finish the golden path before optional work.

## Definition of the golden path

One click starts a real run on the bundled dataset. The UI streams safe tool events, produces roles for all 2,248 nodes, shows a ranked top 20, creates a local review case, exports the required CSV files, and ends with passed verification.

## Phase 0 — repository and contracts

- [x] Import the organizer starter into `starter/`.
- [x] Import the supplied parquet dataset into `data/`.
- [x] Document architecture, data model, tools, agent loop, and analytical rules.
- [x] Add secure `.env.example` and local ignored `.env` placeholders.
- [x] Add `AGENTS.md` project instructions.
- [x] Add SciPy required by starter PageRank.
- [x] Run the starter in a clean Python environment and record runtime in `docs/BASELINE.md`.
- [x] Add a dataset fingerprint/checksum manifest.

## Phase 1 — deterministic core pipeline, P0

- [x] Create `backend/` package and dependency lockfile.
- [x] Implement typed parquet loaders and dataset validation.
- [x] Reconcile transactions with aggregated edges.
- [x] Build the directed weighted graph including orphan nodes.
- [x] Implement structural features from `docs/ANALYTICS.md`.
- [x] Implement date-based supporting temporal features.
- [x] Implement deterministic Louvain clustering with seed 42.
- [x] Implement versioned role assignment and evidence templates.
- [x] Implement priority scoring and deterministic tie-breaking.
- [x] Write all three mandatory CSV files.
- [x] Add unit and regression tests for data traps.
- [x] Verify full pipeline runtime stays below five minutes.

Exit criterion: one Python command creates valid non-empty outputs without OpenAI.

## Phase 2 — storage, tools, and verification, P0

- [x] Add SQLite models/repositories for runs, events, cases, and artifacts.
- [x] Implement atomic artifact writes and SHA-256 hashes.
- [x] Implement the exact strict schemas in `docs/TOOLS.md`.
- [x] Add state-based tool allowlisting.
- [x] Make state-changing tools idempotent.
- [x] Implement `verify_run` independently from the agent.
- [x] Add tool contract and invalid-state tests.

Exit criterion: a deterministic script can execute the complete tool sequence, create a case, export files, and pass verification.

## Phase 3 — agent orchestration, P0

- [ ] Implement the provider-neutral loop from `docs/AGENT_LOOP.md`.
- [ ] Implement `DeterministicDemoProvider`.
- [ ] Implement `OpenAIResponsesProvider` with function calling.
- [ ] Implement Structured Outputs for terminal `AgentDecision`.
- [ ] Add timeout, invalid-response, refusal, and tool-failure handling.
- [ ] Enforce tool budget and retry limits.
- [ ] Persist safe execution events without chain-of-thought.
- [ ] Test missing-key behavior without exposing secrets.

Exit criterion: demo and live providers drive the same tools and reach the same verified analytical result.

## Phase 4 — FastAPI golden path, P0

- [x] Add settings validation and `/health`.
- [x] Add run create, execute, status, event-stream, node, cluster, case, and artifact endpoints.
- [x] Serialize every API GID as a string.
- [x] Add SSE event streaming.
- [x] Add pagination and bounded ego-graph queries.
- [x] Add API integration tests for the golden path (HTTP contract with explicit test doubles).

Phase 4 HTTP implementation is available while Phase 3 is pending. Phases 1–2
now provide analytics and storage, but until the backend/executor adapters are
connected, `/health` reports `backend_ready=false`
and run endpoints return `503 BACKEND_NOT_CONFIGURED`. No test fixture is used as
the product's demo provider. Integration contract and commands: [docs/API.md](docs/API.md).

- [ ] Follow-up after Phase 3 lands: wire the real adapters to Phases 1–3 and run the bundled-data
      HTTP golden path (2,248 nodes, top 20, persisted case, CSVs, passed verification).

Exit criterion: the full workflow can be driven only through documented HTTP endpoints.

## Phase 5 — minimal workflow UI, P0

- [ ] Scaffold React/Vite/TypeScript frontend.
- [ ] Create a landing state explaining the event and the analyst decision.
- [ ] Add one-click bundled demo run.
- [ ] Show run status and safe execution trace.
- [ ] Show top targets with role, priority, evidence, and warnings.
- [ ] Add GID search using string identifiers.
- [ ] Add cluster overview and directed 1–2-hop Cytoscape view.
- [ ] Add node detail drawer.
- [ ] Show before/after case state and verification badge.
- [ ] Add artifact downloads and demo reset.

Exit criterion: a judge understands the value and sees the action within 60–90 seconds.

## Phase 6 — reproducibility and judging, P0

- [ ] Add backend and frontend Dockerfiles.
- [ ] Add `docker-compose.yml` with healthchecks.
- [ ] Make `DEMO_MODE=true` the no-key default.
- [ ] Verify `docker compose up --build` on a clean machine.
- [ ] Replace README status text with only implemented behavior.
- [ ] Document role criteria, limitations, scaling, live mode, and troubleshooting.
- [ ] Generate and commit verified example CSV outputs if permitted by submission rules.
- [ ] Prepare one architecture diagram and demo script.

Exit criterion: an asynchronous judge can clone, start, understand, and verify the project without team assistance.

## Phase 7 — optional polish, only after all P0 exits

- [ ] Add top-N node-removal resilience simulation.
- [ ] Add cycle and repeated-route indicators.
- [ ] Add follow-up data-request suggestions for boundary uncertainty.
- [ ] Add richer cluster hypotheses through OpenAI Structured Outputs.
- [ ] Record a short GIF/video of the golden path.

## Cut list

Drop these first if time is limited:

- generic natural-language graph chat;
- multi-agent architecture;
- arbitrary user dataset upload;
- authentication and multi-user permissions;
- real banking-system integration;
- advanced anomaly ML without labels;
- full-graph browser rendering;
- production million-node implementation.

## Suggested five-hour sequence

| Time | Goal |
|---|---|
| 00:00–01:15 | Deterministic pipeline and valid CSVs |
| 01:15–02:15 | Tools, storage, case action, verification |
| 02:15–03:00 | Demo provider and OpenAI provider |
| 03:00–04:00 | FastAPI and minimum UI |
| 04:00–04:35 | Docker and clean-machine run |
| 04:35–05:00 | README, demo rehearsal, bug buffer |
