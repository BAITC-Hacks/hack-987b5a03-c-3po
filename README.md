# AML Agent

AML Agent turns an anonymized bank-transfer graph into an explainable investigation queue for an AML analyst. It validates the batch, computes deterministic graph evidence, assigns roles, ranks targets, creates a local review case, and verifies the resulting artifacts.

> Current status: the Phase 4 FastAPI HTTP layer, validated settings, SSE, bounded queries, and API contract tests are implemented. Phases 1–3 are being developed separately; their storage and orchestrator must still be connected. The default API starts with `backend_ready=false` and returns `503` for run operations. It does not simulate a successful analytical run. See [TODO.md](TODO.md) and the [HTTP/integration contract](docs/API.md).

## Run the Phase 4 API

From the repository root, with Python 3.12+:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r backend/requirements-api.txt
python -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Open [interactive API documentation](http://127.0.0.1:8000/docs) or [health](http://127.0.0.1:8000/health).
`/health` distinguishes the running HTTP service from whether a backend adapter is connected.
No API key is needed to start the API or run its contract tests.

```bash
python -m pip install -r backend/requirements-api-dev.txt
python -m pytest backend/tests/api -q
python -m ruff check backend/app/api backend/app/config.py backend/app/main.py backend/tests/api
```

These tests use fixtures confined to `backend/tests/api/`. They verify the HTTP
workflow and failure handling, not the analytical correctness or completion of
Phases 1–3. The full bundled-data HTTP integration run remains a follow-up after
those phases land. Dependencies are pinned separately so Phase 4 does not replace
the analytical backend's dependency files. Validated locally on Python 3.14.2.

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

The OpenAI model orchestrates controlled tools and produces schema-constrained summaries. It does not calculate roles, invent metrics, access arbitrary files, or execute shell commands.

## Dataset

The repository includes the anonymized hackathon dataset:

- `data/nodes.parquet`: 2,248 clients;
- `data/edges.parquet`: 3,119 aggregated directed edges;
- `data/transactions.parquet`: 4,840 individual transactions;
- period: 2026-07-01 through 2026-07-31;
- observed turnover: 365,890,012.01 KZT.

See [data/README.md](data/README.md) for fields and collection constraints. The most important limitation is the four-hop boundary: 444 depth-4 nodes have no visible outgoing transfers and must not automatically be labeled as terminal recipients.

## Planned architecture

```text
React / Vite UI
       |
       v
FastAPI application ---- SQLite run/case/audit store
       |
       +---- Agent orchestrator ---- OpenAI Responses API
       |              |
       |              +---- strict function tools
       |
       +---- Deterministic analytics engine
                     |
                     +---- pandas / NetworkX / SciPy
                     +---- parquet input
                     +---- CSV and JSON artifacts
```

Detailed design:

- [Architecture](docs/ARCHITECTURE.md)
- [Data model](docs/DATA_MODEL.md)
- [Analytical rules](docs/ANALYTICS.md)
- [Tool contracts](docs/TOOLS.md)
- [Agent loop](docs/AGENT_LOOP.md)
- [HTTP API and Phase 1–3 integration](docs/API.md)
- [Implementation checklist](TODO.md)

## OpenAI configuration

The live orchestrator will use the OpenAI Responses API with function calling and Structured Outputs. The default model is configurable and currently set to `gpt-5-mini`, which supports the Responses endpoint, function calling, and structured outputs.

1. Copy `.env.example` to `.env` if `.env` does not already exist.
2. Put your existing key in the local ignored file:

   ```dotenv
   OPENAI_API_KEY=your-existing-key
   DEMO_MODE=false
   ```

3. Never paste the key into source code, documentation, issues, logs, screenshots, or commits.

For deterministic offline operation, keep `DEMO_MODE=true`; the graph analysis remains real and only the external model provider is replaced.

Official references: [function calling](https://developers.openai.com/api/docs/guides/function-calling), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), and [`gpt-5-mini`](https://developers.openai.com/api/docs/models/gpt-5-mini).

## Run the supplied starter

The starter validates the parquet files, builds the graph, calculates basic features, and writes empty output templates. It is a baseline, not the finished product.

```bash
python -m venv .venv
pip install -r starter/requirements.txt
python starter/starter.py --data data --out out
```

Expected output:

- `out/nodes_roles.csv`
- `out/clusters.csv`
- `out/top_nodes.csv`

The starter intentionally leaves role assignment, clustering, ranking, and visualization for the implementation.

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
|-- backend/
|   |-- app/
|   |   |-- api/           # HTTP routes, DTOs, SSE, query/execution service, integration ports
|   |   |-- config.py
|   |   `-- main.py        # ASGI factory
|   |-- tests/api/         # HTTP contract tests with explicit test doubles
|   |-- requirements-api.txt
|   `-- requirements-api-dev.txt
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
