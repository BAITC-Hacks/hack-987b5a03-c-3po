# AML Agent backend

Deterministic analytics, storage, controlled tools, and verification for AML Agent.

Implemented modules:

- `aml_agent.analytics`: validated parquet loading, directed graph features, deterministic roles, Louvain clusters, ranking, and byte-stable CSV export;
- `aml_agent.storage`: SQLite state/audit repositories and write-once controlled artifacts;
- `aml_agent.tools`: strict schemas and state allowlists for all ten agent tools;
- `aml_agent.tool_runtime`: deterministic execution, local review-case action, export, and independent verification.

Run the full offline workflow from the repository root:

```powershell
.\.venv\Scripts\aml-agent-tools.exe --data data --database var\aml-agent.sqlite3 --artifacts artifacts --top 20
```

Run only the phase 1 analytical export:

```powershell
.\.venv\Scripts\aml-agent-pipeline.exe --data data --out out --top 20 --expected-seeds 81 --period-start 2026-07-01 --period-end 2026-07-31
```

Run quality checks:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe -m ruff check backend starter\starter.py
```

The public product description and environment setup live in the repository root `README.md`.
