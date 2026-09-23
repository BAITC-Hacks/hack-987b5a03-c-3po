# AML Agent data model

## 1. Identifier rule

Source parquet and CSV files keep GIDs as `int64`. Every API, JSON, SQLite text column, frontend type, and tool argument represents a GID as a decimal string because the supplied values exceed JavaScript's safe integer range.

## 2. Input records

### NodeInput

| Field | Type | Constraint |
|---|---|---|
| `gid` | int64 | Unique in `nodes.parquet` |
| `depth` | int | 0 through 4 |
| `is_seed` | bool | Exactly 81 true values in the supplied dataset |

### EdgeInput

| Field | Type | Constraint |
|---|---|---|
| `src` | int64 | Present in nodes |
| `dst` | int64 | Present in nodes |
| `sum_kzt` | float64 | At least 5,000; finite and positive |
| `n_tx` | int64 | Positive |
| `depth` | int8 | 1 through 4 |

`(src, dst)` is unique because edges are aggregated over the period.

### TransactionInput

| Field | Type | Constraint |
|---|---|---|
| `src` | int64 | Present in nodes |
| `dst` | int64 | Present in nodes |
| `date` | date | Within the declared batch period |
| `sum_kzt` | float64 | At least 5,000; finite and positive |

Transactions grouped by `(src, dst)` must reproduce both `edges.sum_kzt` within 0.01 KZT and `edges.n_tx` exactly.

## 3. Derived analytical records

### NodeFeature

| Field | Type | Meaning |
|---|---|---|
| `run_id` | UUID | Owning analysis run |
| `gid` | string | API-safe client identifier |
| `depth` | int | Minimum observed traversal depth |
| `is_seed` | bool | Seed flag |
| `in_degree` / `out_degree` | int | Distinct counterparties |
| `in_kzt` / `out_kzt` | float | Observed amounts inside the sample |
| `in_tx` / `out_tx` | int | Observed transaction counts |
| `pass_through_ratio` | float or null | `out_kzt / in_kzt`; null when unsupported |
| `pagerank` | float | Directed amount-weighted PageRank |
| `betweenness` | float | Deterministically sampled unweighted directed betweenness |
| `seed_reach_count` | int | Number of seed nodes with a directed path to this node |
| `rapid_outflow_ratio` | float or null | Share of outflow within two calendar days of any observed inflow |
| `truncated_by_depth` | bool | Depth 4 with no visible outgoing edge |
| `uncertainty_flags` | string array | Explicit data limitations for this node |

The temporal signal is supporting evidence only because transactions have dates but no timestamps.

### NodeAssessment

| Field | Type | Constraint |
|---|---|---|
| `run_id` | UUID | Foreign key to run |
| `gid` | string | Unique within run |
| `role` | enum | `consolidator`, `transit`, `distributor`, `terminal`, `coordinator`, `peripheral` |
| `role_score` | float | 0 through 1, rule strength rather than guilt probability |
| `cluster_id` | int | Required for every node |
| `priority_score` | float | 0 through 1 |
| `evidence` | string | Non-empty, at most 200 characters, includes calculated values |
| `ruleset_version` | string | Initially `v1` |

Role precedence for `v1` is `coordinator`, `consolidator`, `distributor`, `transit`, `terminal`, then `peripheral`. Boundary truncation prevents a terminal assignment based only on missing outflow.

### ClusterAssessment

| Field | Type | Constraint |
|---|---|---|
| `run_id` | UUID | Owning run |
| `cluster_id` | int | Unique within run |
| `n_nodes` | int | Positive |
| `n_seed` | int | Non-negative |
| `sum_kzt_internal` | float | Non-negative |
| `top_gids` | string array | Ranked identifiers |
| `hypothesis` | string | Cautious, evidence-based description |
| `algorithm` | string | `louvain` for MVP |
| `random_seed` | int | Fixed at 42 for reproducibility |

### RankedTarget

| Field | Type | Constraint |
|---|---|---|
| `rank` | int | Starts at 1, no gaps |
| `gid` | string | Unique within the list |
| `role` | role enum | Copied from assessment |
| `priority_score` | float | Descending order |
| `why` | string | Numeric, cautious explanation |

## 4. Operational records

### AnalysisRun

| Field | Type | Meaning |
|---|---|---|
| `run_id` | UUID | Primary key |
| `dataset_id` | string | Logical dataset reference, not a path |
| `dataset_sha256` | string | Reproducibility fingerprint |
| `mode` | enum | `demo` or `live` |
| `status` | enum | State machine value |
| `ruleset_version` | string | Analytical rule version |
| `model` | string or null | OpenAI model in live mode |
| `created_at` / `updated_at` | datetime | UTC timestamps |
| `warning_count` | int | Number of persisted warnings |
| `verification_status` | enum | `pending`, `passed`, or `failed` |

Run states:

```text
created -> validated -> graph_ready -> analyzed -> classified
        -> ranked -> case_created -> verified -> completed

Any state may transition to failed; ranked/case_created may transition to
verification_failed. A completed run is immutable.
```

### AgentEvent

| Field | Type | Meaning |
|---|---|---|
| `event_id` | UUID | Primary key |
| `run_id` | UUID | Owning run |
| `sequence` | int | Monotonic per run |
| `kind` | enum | `started`, `tool_started`, `tool_completed`, `decision`, `action`, `warning`, `verification`, `failed`, `completed` |
| `tool_name` | string or null | Registered tool only |
| `summary` | string | Safe UI trace, no chain-of-thought |
| `payload_json` | JSON | Sanitized structured metadata |
| `created_at` | datetime | UTC timestamp |

### ReviewCase

| Field | Type | Meaning |
|---|---|---|
| `case_id` | UUID | Primary key |
| `run_id` | UUID | Source run, unique for MVP |
| `title` | string | Human-readable case label |
| `status` | enum | `ready_for_review`, `in_review`, `closed` |
| `target_gids` | string array | Immutable target snapshot |
| `created_by` | enum | `agent` or `analyst` |
| `created_at` | datetime | UTC timestamp |

### Artifact

| Field | Type | Meaning |
|---|---|---|
| `artifact_id` | UUID | Primary key |
| `run_id` | UUID | Owning run |
| `name` | enum | `nodes_roles.csv`, `clusters.csv`, `top_nodes.csv`, `audit.json` |
| `relative_path` | string | Path beneath the run artifact directory |
| `sha256` | string | Integrity fingerprint |
| `row_count` | int or null | CSV verification value |

## 5. CSV contracts

### nodes_roles.csv

`gid,role,role_score,cluster_id,priority_score,evidence`

Exactly 2,248 rows for the supplied dataset. Extra analytical columns may be appended, but required columns cannot be renamed or removed.

### clusters.csv

`cluster_id,n_nodes,n_seed,sum_kzt_internal,top_gids,hypothesis`

### top_nodes.csv

`rank,gid,role,priority_score,why`

At least 20 rows in descending priority order.

## 6. Core invariants

1. A run cannot be completed unless verification passes.
2. Every input node has exactly one NodeAssessment and cluster ID.
3. Model-generated text cannot change an analytical value.
4. Evidence is reproducible from persisted features and ruleset version.
5. Orphan seed nodes remain in outputs and receive a documented low-evidence assessment.
6. Re-running the same dataset and ruleset produces the same analytical outputs.
