# AML Agent

AML Agent turns an anonymized bank-transfer graph into an explainable investigation queue for an AML analyst. It validates the batch, computes deterministic graph evidence, assigns roles, ranks targets, creates a local review case, and verifies the resulting artifacts.

> Current status: architecture and starter baseline are committed. The production pipeline, API, agent orchestrator, and UI are the next implementation stages tracked in [TODO.md](TODO.md).

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
