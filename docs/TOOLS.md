# AML Agent tool contracts

## 1. Contract rules

- Tools are application functions exposed to the OpenAI Responses API.
- Every input uses strict JSON Schema with `additionalProperties: false`.
- The orchestrator receives only tools allowed for the current run state.
- Tools accept logical IDs, never arbitrary filesystem paths, SQL, or code.
- All GIDs are decimal strings in JSON.
- A tool validates its arguments again at execution time; schema validation is not the only security boundary.
- Analytics tools are deterministic and idempotent for the same run and ruleset.
- A transport retry may replay a persisted result only for the exact same arguments in that tool's immediate successor state. Replays from later states or `completed` are rejected.
- Review-case titles are selected from a fixed server-approved cautious allowlist; arbitrary model wording is rejected.

## 2. Standard result envelope

Every tool returns this application-side JSON shape:

```json
{
  "ok": true,
  "tool": "inspect_dataset",
  "run_id": "6ba7b810-9dad-4af6-8640-708e9e8d052f",
  "summary": "Dataset is valid: 2,248 nodes, 3,119 edges, 4,840 transactions.",
  "data": {},
  "warnings": [],
  "artifacts": [],
  "error": null
}
```

On failure, `ok` is false and `error` is:

```json
{
  "code": "DATASET_SCHEMA_INVALID",
  "message": "nodes.parquet is missing is_seed.",
  "retriable": false,
  "details": {}
}
```

The error allowlist is `INVALID_STATE`, `INVALID_ARGUMENT`, `DATASET_NOT_FOUND`, `DATASET_SCHEMA_INVALID`, `DATASET_INCONSISTENT`, `ANALYTICS_FAILED`, `CASE_WRITE_FAILED`, `EXPORT_FAILED`, `VERIFICATION_FAILED`, and `INTERNAL_ERROR`.

## 3. Responses API function definitions

The canonical input definitions are:

```json
[
  {
    "type": "function",
    "name": "inspect_dataset",
    "description": "Validate the run's three parquet files, reconcile transactions with aggregated edges, fingerprint the dataset, and record data limitations. Does not build or score the graph.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "run_id": {
          "type": "string",
          "format": "uuid",
          "description": "Existing analysis run in created state."
        }
      },
      "required": ["run_id"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "build_graph",
    "description": "Build and persist the directed weighted graph for a validated run, including isolated input nodes and boundary flags.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "run_id": {
          "type": "string",
          "format": "uuid",
          "description": "Run in validated state."
        }
      },
      "required": ["run_id"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "compute_graph_features",
    "description": "Compute deterministic structural features and, when requested and available, supporting temporal signals. Does not assign roles.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "run_id": {
          "type": "string",
          "format": "uuid",
          "description": "Run in graph_ready state."
        },
        "include_temporal": {
          "type": "boolean",
          "description": "Whether to calculate date-based rapid-outflow signals from transactions.parquet."
        }
      },
      "required": ["run_id", "include_temporal"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "cluster_network",
    "description": "Assign every node to a reproducible Louvain community using an undirected projection only for community detection, while preserving directed features for roles.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "run_id": {
          "type": "string",
          "format": "uuid",
          "description": "Run with computed graph features."
        },
        "resolution": {
          "type": "number",
          "minimum": 0.5,
          "maximum": 2.0,
          "description": "Louvain resolution; MVP uses 1.0."
        },
        "random_seed": {
          "type": "integer",
          "minimum": 0,
          "maximum": 2147483647,
          "description": "Deterministic random seed; MVP uses 42."
        }
      },
      "required": ["run_id", "resolution", "random_seed"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "assign_roles",
    "description": "Apply the documented deterministic ruleset to every node, producing one role, rule-strength score, numeric evidence, and uncertainty flags.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "run_id": {
          "type": "string",
          "format": "uuid",
          "description": "Run with features and cluster assignments."
        },
        "ruleset_version": {
          "type": "string",
          "enum": ["v1"],
          "description": "Immutable role and scoring ruleset version."
        }
      },
      "required": ["run_id", "ruleset_version"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "rank_targets",
    "description": "Calculate priority scores from persisted evidence and produce an ordered review list. The result is a triage priority, not a guilt probability.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "run_id": {
          "type": "string",
          "format": "uuid",
          "description": "Run with complete node assessments."
        },
        "limit": {
          "type": "integer",
          "minimum": 20,
          "maximum": 100,
          "description": "Number of ranked targets; golden path uses 20."
        }
      },
      "required": ["run_id", "limit"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "get_node_evidence",
    "description": "Return calculated evidence, uncertainty, cluster context, and a bounded directed ego graph for one GID. This tool is read-only.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "run_id": {
          "type": "string",
          "format": "uuid",
          "description": "Analyzed run identifier."
        },
        "gid": {
          "type": "string",
          "pattern": "^[0-9]+$",
          "description": "Client GID encoded as a decimal string."
        },
        "neighbor_hops": {
          "type": "integer",
          "minimum": 1,
          "maximum": 2,
          "description": "Maximum ego-graph radius."
        }
      },
      "required": ["run_id", "gid", "neighbor_hops"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "create_review_case",
    "description": "Create one local analyst review case from the complete ranked target snapshot. This does not block accounts or contact an external system.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "run_id": {
          "type": "string",
          "format": "uuid",
          "description": "Run in ranked state."
        },
        "target_gids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[0-9]+$"
          },
          "minItems": 20,
          "maxItems": 100,
          "description": "Ordered target snapshot; each GID must exist in the run's ranking."
        },
        "title": {
          "type": "string",
          "enum": [
            "Priority structural indicators for analyst review",
            "Network indicators for analyst review",
            "Структурные индикаторы для проверки аналитиком"
          ],
          "description": "Server-approved cautious analyst-facing case title."
        }
      },
      "required": ["run_id", "target_gids", "title"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "export_results",
    "description": "Write the three required CSV files and an optional JSON audit bundle to the run's controlled artifact directory.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "run_id": {
          "type": "string",
          "format": "uuid",
          "description": "Run with a persisted review case."
        },
        "include_audit": {
          "type": "boolean",
          "description": "Whether to include audit.json alongside the mandatory CSV files."
        }
      },
      "required": ["run_id", "include_audit"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "verify_run",
    "description": "Independently recompute authoritative analytics and verify database invariants, CSV schemas and counts, roles, scores, evidence, clusters, ranking order, artifact hashes, and review-case consistency.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "run_id": {
          "type": "string",
          "format": "uuid",
          "description": "Run with exported artifacts."
        }
      },
      "required": ["run_id"],
      "additionalProperties": false
    }
  }
]
```

## 4. Tool-specific result data

| Tool | Required `data` fields |
|---|---|
| `inspect_dataset` | `dataset_sha256`, `n_nodes`, `n_edges`, `n_transactions`, `n_seed`, `turnover_kzt`, `period`, `limitations` |
| `build_graph` | `n_nodes`, `n_edges`, `n_components_with_edges`, `n_orphan_nodes`, `n_truncated_depth4` |
| `compute_graph_features` | `feature_version`, `computed_fields`, `temporal_enabled`, `warning_counts` |
| `cluster_network` | `algorithm`, `resolution`, `random_seed`, `n_clusters`, `n_multi_seed_clusters` |
| `assign_roles` | `ruleset_version`, `assigned_count`, `role_counts`, `uncertain_count` |
| `rank_targets` | `limit`, `ranked_count`, `target_gids`, `score_range` |
| `get_node_evidence` | `node`, `incoming_edges`, `outgoing_edges`, `cluster`, `uncertainty_flags` |
| `create_review_case` | `case_id`, `status`, `target_count`, `idempotent_replay` |
| `export_results` | `artifact_names`, `sha256_by_name`, `row_count_by_name` |
| `verify_run` | `passed`, `checks`, `failed_checks`, `completed_at` |

## 5. State-based allowlist

| Run state | Tools exposed to the model |
|---|---|
| `created` | `inspect_dataset` |
| `validated` | `build_graph` |
| `graph_ready` | `compute_graph_features` |
| `analyzed` | `cluster_network` |
| `clustered` | `assign_roles` |
| `classified` | `rank_targets`, `get_node_evidence` |
| `ranked` | `get_node_evidence`, `create_review_case` |
| `case_created` | `get_node_evidence`, `export_results` |
| `exported` | `verify_run` |
| `verified`, `completed` | `get_node_evidence` only |
| `verification_failed`, `failed` | none |

The backend rejects any tool call that does not match the persisted state even if the model attempts it.

An exact invocation replay in the immediate successor state is an internal idempotency exception for transport/crash recovery; it is not exposed as an available model tool. Recovery uses immutable argument intent and, when available, the persisted result. A completed run accepts only read-only `get_node_evidence`, which is evaluated without writing events or cached results.

The application also enforces unique `target_gids` and an exact match to the persisted ranked snapshot. `uniqueItems` is intentionally absent from the API-facing strict schema because it is outside the supported Structured Outputs subset; application validation remains authoritative.
