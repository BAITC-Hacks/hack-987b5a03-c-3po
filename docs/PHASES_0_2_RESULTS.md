# Phases 0–2 verification record

Date: 2026-09-23

## Implemented scope

- Phase 0: repository contracts, bundled data, clean environment, starter baseline, and dataset checksum regression.
- Phase 1: deterministic validation, graph features, communities, roles, priority ranking, and the three required CSV exports.
- Phase 2: strict tool schemas, persisted state machine, SQLite audit trail, idempotent local review case, controlled artifacts, and independent verification.

OpenAI orchestration, FastAPI, UI, and Docker are intentionally outside this checkpoint and remain in phases 3–6.

## Bundled dataset result

```text
dataset fingerprint: 0f3f4e66909277f7a6023aa98a143bc14923329a54d2f6fd7f618b3ac945e954
ruleset:            v1
node assessments:   2,248
clusters:           91
ranked targets:     20
depth-4 flags:      444
review cases:       1 per run
verification:       19 checks passed
```

The complete phase 2 tool workflow ends in `completed` with `verification_status=passed` and writes `nodes_roles.csv`, `clusters.csv`, `top_nodes.csv`, and `audit.json` beneath the run UUID.

Deterministic role counts for ruleset `v1` are 11 coordinators, 22 consolidators, 62 distributors, 28 transit nodes, 135 terminals, and 1,990 peripheral nodes. These are structural review hypotheses, not labels of guilt.

## Determinism evidence

Two consecutive phase 1 runs over the same dataset produced byte-identical CSV files:

| Artifact | SHA-256 |
|---|---|
| `nodes_roles.csv` | `4E83F5B60FBA212D485AF74FE2ED37C43B4072E8476BA27202C53A9A00D7620B` |
| `clusters.csv` | `D92D723D3291366BA6A00FA3CFD58C9D9DA9C54811DD0D778A306557318C8A79` |
| `top_nodes.csv` | `E7E1D30EE1D8996FA610BCC6A259F90C6E5C1077F747ABC03EE3CEE7D72F3E8C` |

Observed phase 1 runtime on the development machine was approximately 1.2–1.6 seconds, well below the five-minute limit.

## Quality gates

- all input GIDs receive exactly one assessment and cluster;
- 19 orphan seeds remain in the result;
- no depth-4 truncation is labeled terminal only because outflow is absent;
- seed pass-through remains unsupported;
- declared transaction period and edge reconciliation are validated;
- ranking uses numeric GID tie-breaking and cannot request fewer than 20 targets;
- tool inputs use strict schemas and persisted state allowlists;
- completed runs and artifact content are immutable through the storage interfaces;
- artifact tampering causes independent verification to fail;
- verification independently recomputes roles, scores, evidence, clusters, and ranking from the registered parquet data;
- the offline workflow requires no API key.

Run the complete test suite with:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```
