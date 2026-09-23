# AML Agent

AML Agent turns an anonymized bank-transfer graph into an explainable investigation queue for an AML analyst. It validates the batch, computes deterministic graph evidence, assigns roles, ranks targets, creates a local review case, and verifies the resulting artifacts.

> Current status: phases 0–3 are implemented. The deterministic pipeline and bounded agent orchestration run with real analytics, controlled tools, SQLite audit, a local review case, and independent verification. HTTP API, UI, and Docker remain in phases 4–6 of [TODO.md](TODO.md).

## Problem

An AML analyst starts with 81 known seed clients but must manually trace a four-hop network of 2,248 accounts. The useful decision is not a generic summary: it is **which accounts should be reviewed first, why, and what evidence supports that priority**.

AML Agent is decision support. Roles and priorities are hypotheses for analyst review, not findings of guilt and not instructions to block an account.

## Target workflow

```text
Parquet batch
  -> validate data and limitations
  -> build directed weighted graph
  -> compute structural and temporal evidence
  -> cluster and assign explainable roles
  -> rank review targets
  -> create a local AML review case
  -> export and verify required artifacts
```

In live mode, the OpenAI model selects controlled tools and returns a schema-constrained terminal decision. The deterministic backend calculates roles and metrics, creates the case, and verifies the outputs.

## Implemented golden path (CLI)

The current offline workflow executes nine state-changing/verification steps; the tenth controlled tool, `get_node_evidence`, is available on demand:

```text
inspect_dataset -> build_graph -> compute_graph_features -> cluster_network
-> assign_roles -> rank_targets -> create_review_case -> export_results
-> verify_run -> completed
```

On the bundled dataset it produces 2,248 node assessments, 91 cluster summaries, a ranked top 20, one local review case, three mandatory CSV files, `audit.json`, and a 19-check verification report. Demo mode needs no API key.

### Quick start on Windows

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e backend
.\.venv\Scripts\aml-agent-tools.exe --data data --database var\aml-agent.sqlite3 --artifacts artifacts --top 20
.\.venv\Scripts\aml-agent-run.exe --mode demo --data data --database var\agent.sqlite3 --artifacts artifacts
```

### Quick start on macOS/Linux

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e backend
.venv/bin/aml-agent-tools --data data --database var/aml-agent.sqlite3 --artifacts artifacts --top 20
.venv/bin/aml-agent-run --mode demo --data data --database var/agent.sqlite3 --artifacts artifacts
```

A successful agent run returns `status: completed`; the persisted run has `verification_status: passed`, and all nine workflow tools have `ok: true` results. Each invocation creates a new run record.

## Dataset

The repository includes the anonymized hackathon dataset:

- `data/nodes.parquet`: 2,248 clients;
- `data/edges.parquet`: 3,119 aggregated directed edges;
- `data/transactions.parquet`: 4,840 individual transactions;
- period: 2026-07-01 through 2026-07-31;
- observed turnover: 365,890,012.01 KZT.

See [data/README.md](data/README.md) for fields and collection constraints. The most important limitation is the four-hop boundary: 444 depth-4 nodes have no visible outgoing transfers and must not automatically be labeled as terminal recipients.

## Architecture

```text
React / Vite UI (phase 5)
       |
       v
FastAPI application (phase 4) ---- SQLite run/case/audit store [implemented]
       |
       +---- Agent orchestrator [implemented] ---- OpenAI Responses API
       |              |
       |              +---- strict function tools [implemented]
       |
       +---- Deterministic analytics engine [implemented]
                     |
                     +---- pandas / NetworkX / SciPy
                     +---- parquet input
                     +---- CSV and JSON artifacts
```

Detailed design:

- [Architecture](docs/ARCHITECTURE.md)
- [Phase 0 baseline](docs/BASELINE.md)
- [Phases 0–2 verification record](docs/PHASES_0_2_RESULTS.md)
- [Data model](docs/DATA_MODEL.md)
- [Analytical rules](docs/ANALYTICS.md)
- [Tool contracts](docs/TOOLS.md)
- [Agent loop](docs/AGENT_LOOP.md)
- [Implementation checklist](TODO.md)

## OpenAI configuration

The live orchestrator uses the OpenAI Responses API with function calling and Structured Outputs. The default model is configurable through `OPENAI_MODEL`; it is not hardcoded into analytics or tools. Install the optional live dependencies before using `--mode live`:

```bash
.venv/bin/python -m pip install -e 'backend[live]'
.venv/bin/aml-agent-run --mode live --data data --database var/live.sqlite3 --artifacts artifacts
```

1. Copy `.env.example` to `.env` if `.env` does not already exist.
2. Put your existing key in the local ignored file:

   ```dotenv
   OPENAI_API_KEY=your-existing-key
   DEMO_MODE=false
   ```

3. Run the live command from the repository root so the CLI can load the ignored `.env` file. Never paste the key into source code, logs, or commits.

The CLI defaults to `--mode demo`; the graph analysis remains real and only the external model provider is replaced.

The automated suite uses a scripted Responses transport for live-mode integration and makes no billable API request. The demo CLI needs no key.

Official references: [function calling](https://developers.openai.com/api/docs/guides/function-calling), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), and [`gpt-5-mini`](https://developers.openai.com/api/docs/models/gpt-5-mini).

## Run the supplied starter

The starter validates the parquet files, builds the graph, calculates basic features, and writes empty output templates. It is a baseline, not the finished product.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r starter/requirements.txt
.venv/bin/python starter/starter.py --data data --out out
```

On Windows, replace `.venv/bin/python` with `.\.venv\Scripts\python.exe`.

Expected output:

- `out/nodes_roles.csv`
- `out/clusters.csv`
- `out/top_nodes.csv`

The starter intentionally leaves role assignment, clustering, ranking, and visualization for the implementation.

## Phase 3 orchestration component

`backend/aml_agent/agent/` contains the bounded run loop, deterministic demo provider, OpenAI Responses adapter, strict tool-call validation, and adapters to the production SQLite audit store and tool runtime. Demo mode executes real analytics and case creation. The integration test compares demo and scripted live-provider results over the bundled parquet files.

Run the component tests with:

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
```

Live mode requires the optional `backend[live]` dependencies and `OPENAI_API_KEY` in the environment or ignored `.env`. No live API request is part of the automated tests.

## Required final artifacts

- `nodes_roles.csv`: one row for every one of the 2,248 nodes;
- `clusters.csv`: cluster statistics and a cautious hypothesis;
- `top_nodes.csv`: at least 20 ranked review targets;
- browser UI with graph direction, roles, clusters, GID search, execution trace, and before/after state;
- audit record containing dataset hash, ruleset version, tool events, warnings, and verification result.

## Safety and explainability

- GIDs are serialized as strings in JSON because their values exceed JavaScript's safe integer range. CSV/parquet retain `int64`.
- Seed inflows are incomplete, so their pass-through ratio is not used blindly.
- A depth-4 node with no visible outgoing edge is flagged `truncated_by_depth`, not automatically labeled `terminal`.
- `role_score` is rule strength, not a probability of criminal activity.
- Every evidence string must cite calculated numbers and stay within 200 characters.
- External or destructive actions are outside MVP scope; the only write action is creating a local analyst review case and export bundle.

## Project structure

```text
.
|-- AGENTS.md
|-- TODO.md
|-- data/
|   |-- README.md
|   |-- edges.parquet
|   |-- nodes.parquet
|   `-- transactions.parquet
|-- docs/
|   |-- AGENT_LOOP.md
|   |-- ANALYTICS.md
|   |-- ARCHITECTURE.md
|   |-- DATA_MODEL.md
|   `-- TOOLS.md
|-- backend/
|   |-- aml_agent/
|   |   |-- agent/
|   |   |-- analytics/
|   |   |-- storage/
|   |   |-- tools/
|   |   `-- tool_runtime.py
|   |-- tests/
|   |-- pyproject.toml
|   `-- requirements.lock
|-- starter/
|   |-- README.md
|   |-- requirements.txt
|   `-- starter.py
|-- .env.example
`-- README.md
```

## Scope boundaries

The hackathon MVP will not include automatic account blocking, regulatory submission, external enrichment, arbitrary code execution, a generic chat interface, multi-agent orchestration, or production-scale processing of a million-node graph.

For a future million-node deployment, the NetworkX analytics layer would move to a graph-processing engine or distributed analytical database while preserving the tool and API contracts.
