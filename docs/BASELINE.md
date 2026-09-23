# Phase 0 baseline

## Environment

- Date: 2026-09-23
- Platform: Windows, local workspace
- Python: 3.12 virtual environment in ignored `.venv/`
- Dependencies: `backend/requirements.lock`

## Organizer starter

Command:

```powershell
.\.venv\Scripts\python.exe starter\starter.py --data data --out tmp\starter-out
```

Observed wall time: approximately **2.7 seconds** on the development machine.

Observed input checks:

- nodes: 2,248;
- edges: 3,119;
- transactions: 4,840;
- seed clients: 81;
- orphan seed nodes: 19;
- depth-4 truncated nodes: 444;
- weakly connected components with edges: 16;
- edge/transaction pair reconciliation: passed.

Starter outputs are intentionally incomplete:

- `nodes_roles.csv`: 2,248 rows with empty role fields;
- `clusters.csv`: 0 rows;
- `top_nodes.csv`: 0 rows.

The organizer starter initially failed on a Windows CP1251 terminal because it printed a Unicode arrow. The display-only arrow was replaced with ASCII `->`; analytical behavior was unchanged.

This baseline proves the supplied data and dependency stack load successfully. Phase 1 must replace the empty templates with complete deterministic results.
