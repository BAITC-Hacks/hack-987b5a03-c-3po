"""Controlled Phase 2 tool execution for AML Agent.

The runtime is the application boundary between strict agent-facing function
calls, deterministic analytics, SQLite state, and allowlisted artifacts.  It
does not expose filesystem paths, SQL, or arbitrary code to a model.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import zipfile
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import date as Date
from pathlib import Path
from typing import Any, Final
from uuid import UUID
from xml.etree import ElementTree

import networkx as nx
import numpy as np
import pandas as pd
from jsonschema import ValidationError as SchemaValidationError

from aml_agent.analytics import (
    ROLES,
    RULESET_VERSION,
    DatasetTables,
    DatasetValidationError,
    assign_roles,
    build_directed_graph,
    cluster_network,
    compute_node_features,
    load_dataset,
    score_and_rank,
)
from aml_agent.analytics.clustering import summarize_clusters
from aml_agent.analytics.exports import (
    CLUSTERS_COLUMNS,
    NODES_ROLES_COLUMNS,
    TOP_NODES_COLUMNS,
)
from aml_agent.analytics.review_workbook import (
    REPORT_ARTIFACT_NAME,
    REPORT_SHEET_NAMES,
    build_review_workbook,
)
from aml_agent.storage import (
    MANDATORY_ARTIFACT_NAMES,
    READER_ARTIFACT_NAMES,
    ArtifactStore,
    ArtifactStoreError,
    Database,
    EventKind,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    RecordNotFoundError,
    RunMode,
    RunState,
    StorageError,
)
from aml_agent.storage import (
    ValidationError as StorageValidationError,
)
from aml_agent.tools import get_allowed_tool_names, validate_tool_arguments

JsonObject = dict[str, Any]

_GID = re.compile(r"^[0-9]+$")
_NUMBER = re.compile(r"\d")
_STATEFUL_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "inspect_dataset",
        "build_graph",
        "compute_graph_features",
        "cluster_network",
        "assign_roles",
        "rank_targets",
        "create_review_case",
        "export_results",
        "verify_run",
    }
)
_ATOMIC_TRANSITIONS: Final[Mapping[str, tuple[RunState, RunState]]] = {
    "inspect_dataset": (RunState.CREATED, RunState.VALIDATED),
    "build_graph": (RunState.VALIDATED, RunState.GRAPH_READY),
    "compute_graph_features": (RunState.GRAPH_READY, RunState.ANALYZED),
    "cluster_network": (RunState.ANALYZED, RunState.CLUSTERED),
    "assign_roles": (RunState.CLUSTERED, RunState.CLASSIFIED),
    "rank_targets": (RunState.CLASSIFIED, RunState.RANKED),
}
_IDEMPOTENT_REPLAY_STATES: Final[Mapping[str, RunState]] = {
    **{name: target for name, (_expected, target) in _ATOMIC_TRANSITIONS.items()},
    "create_review_case": RunState.CASE_CREATED,
    "export_results": RunState.EXPORTED,
    # A successful verify may be persisted before the final COMPLETED
    # transition if the process is interrupted.
    "verify_run": RunState.VERIFIED,
}
_RETRIABLE_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {"ANALYTICS_FAILED", "CASE_WRITE_FAILED", "EXPORT_FAILED", "INTERNAL_ERROR"}
)
_NODES_HEADERS: Final[tuple[str, ...]] = tuple(NODES_ROLES_COLUMNS)
_CLUSTERS_HEADERS: Final[tuple[str, ...]] = tuple(CLUSTERS_COLUMNS)
_TOP_HEADERS: Final[tuple[str, ...]] = tuple(TOP_NODES_COLUMNS)


@dataclass(slots=True)
class _RunCache:
    dataset: DatasetTables
    graph: nx.DiGraph | None = None
    features: pd.DataFrame | None = None
    cluster_assignments: pd.DataFrame | None = None
    role_assessments: pd.DataFrame | None = None
    assessments: pd.DataFrame | None = None
    clusters: pd.DataFrame | None = None
    top_nodes: pd.DataFrame | None = None
    temporal_enabled: bool = True
    resolution: float = 1.0
    random_seed: int = 42
    rank_limit: int = 20


@dataclass(frozen=True, slots=True)
class _Outcome:
    summary: str
    data: JsonObject
    warnings: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    ok: bool = True
    error_code: str | None = None
    error_message: str | None = None
    error_details: JsonObject = field(default_factory=dict)


class _ToolFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retriable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retriable = retriable
        self.details = dict(details or {})


class ToolRuntime:
    """Execute only documented tools against trusted datasets and storage."""

    def __init__(
        self,
        database: Database,
        artifact_store: ArtifactStore,
        dataset_registry: Mapping[str, str | Path],
        *,
        expected_seed_counts: Mapping[str, int | None] | None = None,
        expected_periods: Mapping[str, tuple[str | Date, str | Date] | None] | None = None,
    ) -> None:
        if not dataset_registry:
            raise ValueError("dataset_registry cannot be empty")
        self.database = database
        self.artifact_store = artifact_store
        self._dataset_registry = {
            dataset_id: Path(path).expanduser().resolve(strict=False)
            for dataset_id, path in dataset_registry.items()
        }
        if any(not _logical_dataset_id(dataset_id) for dataset_id in self._dataset_registry):
            raise ValueError("dataset_registry contains an unsafe logical identifier")
        supplied_counts = dict(expected_seed_counts or {})
        unknown_counts = set(supplied_counts) - set(self._dataset_registry)
        if unknown_counts:
            raise ValueError("expected_seed_counts contains an unregistered dataset")
        if any(value is not None and value < 0 for value in supplied_counts.values()):
            raise ValueError("expected seed counts must be non-negative")
        self._expected_seed_counts = supplied_counts
        supplied_periods = dict(expected_periods or {})
        unknown_periods = set(supplied_periods) - set(self._dataset_registry)
        if unknown_periods:
            raise ValueError("expected_periods contains an unregistered dataset")
        if any(
            period is not None and (not isinstance(period, tuple) or len(period) != 2)
            for period in supplied_periods.values()
        ):
            raise ValueError("each expected period must contain start and end dates")
        self._expected_periods = supplied_periods
        self._cache: dict[str, _RunCache] = {}

    def create_run(
        self,
        dataset_id: str,
        mode: RunMode | str = RunMode.DEMO,
        *,
        run_id: str | None = None,
        model: str | None = None,
    ):
        """Create a run only for a configured logical dataset."""

        if dataset_id not in self._dataset_registry:
            raise KeyError(f"Unknown dataset_id: {dataset_id!r}")
        return self.database.create_run(
            dataset_id,
            mode,
            run_id=run_id,
            ruleset_version=RULESET_VERSION,
            model=model,
            metadata={"dataset_source": "registered"},
        )

    def execute(self, tool_name: str, arguments: Any) -> JsonObject:
        """Validate and execute one tool, always returning the standard envelope."""

        safe_run_id = _safe_run_id(arguments)
        try:
            validated = validate_tool_arguments(tool_name, arguments)
        except KeyError:
            return self._error_envelope(
                tool_name,
                safe_run_id,
                "INVALID_ARGUMENT",
                "Unknown tool name.",
            )
        except SchemaValidationError as exc:
            return self._error_envelope(
                tool_name,
                safe_run_id,
                "INVALID_ARGUMENT",
                "Tool arguments do not match the strict schema.",
                details={"field": _schema_error_field(exc)},
            )

        run_id = str(UUID(validated["run_id"]))
        validated["run_id"] = run_id
        idempotency_key = _argument_hash(validated)
        try:
            run = self.database.require_run(run_id)
            stored = self.database.get_tool_result(
                run_id,
                tool_name,
                idempotency_key=idempotency_key,
            )
            self._authorize_and_claim(
                run,
                tool_name,
                validated,
                idempotency_key,
                exact_replay=stored is not None,
            )

            # Completed runs are immutable. Evidence remains available, but is
            # recomputed ephemerally without events, snapshots, or tool results.
            if tool_name == "get_node_evidence" and run.status is RunState.COMPLETED:
                return self._outcome_envelope(
                    tool_name,
                    run_id,
                    self._dispatch(tool_name, validated),
                )

            if stored is not None:
                if tool_name == "verify_run" and run.status is RunState.VERIFIED:
                    self._finish_verified_run(run_id, stored.payload)
                return deepcopy(stored.payload)
            self.database.append_event(
                run_id,
                EventKind.TOOL_STARTED,
                f"{tool_name} started",
                tool_name=tool_name,
                payload=_safe_event_arguments(validated),
            )
            outcome = self._dispatch(tool_name, validated)
            for warning in outcome.warnings:
                self.database.append_event(
                    run_id,
                    EventKind.WARNING,
                    warning,
                    tool_name=tool_name,
                    payload={"source": "tool_result"},
                )
            envelope = self._outcome_envelope(tool_name, run_id, outcome)
            self._persist_result(tool_name, validated, idempotency_key, envelope)
            self.database.append_event(
                run_id,
                EventKind.TOOL_COMPLETED,
                outcome.summary,
                tool_name=tool_name,
                payload={
                    "ok": outcome.ok,
                    "warning_count": len(outcome.warnings),
                    "artifact_names": list(outcome.artifacts),
                    **({"error_code": outcome.error_code} if outcome.error_code else {}),
                },
            )
            if tool_name == "verify_run" and outcome.ok:
                self._finish_verified_run(run_id, envelope)
            return envelope
        except Exception as exc:  # errors are normalized and never expose tracebacks
            failure = self._normalize_exception(exc)
            envelope = self._error_envelope(
                tool_name,
                run_id,
                failure.code,
                failure.message,
                retriable=failure.retriable,
                details=failure.details,
            )
            try:
                self.database.append_event(
                    run_id,
                    EventKind.TOOL_COMPLETED,
                    f"{tool_name} failed safely",
                    tool_name=tool_name,
                    payload={"ok": False, "error_code": failure.code},
                )
            except Exception:
                pass
            return envelope

    def _finish_verified_run(self, run_id: str, result: Mapping[str, Any]) -> None:
        """Persist terminal trace events before the immutable completed transition."""

        events = self.database.list_events(run_id)
        if not any(
            event.kind is EventKind.TOOL_COMPLETED and event.tool_name == "verify_run"
            for event in events
        ):
            self.database.append_event(
                run_id,
                EventKind.TOOL_COMPLETED,
                str(result.get("summary", "Independent verification passed")),
                tool_name="verify_run",
                payload={"ok": True, "warning_count": 0, "artifact_names": []},
            )
        if not any(event.kind is EventKind.VERIFICATION for event in events):
            self.database.append_event(
                run_id,
                EventKind.VERIFICATION,
                "Independent verification passed",
                payload={"verification_passed": True},
            )
        if not any(event.kind is EventKind.COMPLETED for event in events):
            self.database.append_event(
                run_id,
                EventKind.COMPLETED,
                "Run completed after independent verification",
                payload={"verification_passed": True},
            )
        self.database.complete_run(run_id)

    def _authorize_and_claim(
        self,
        run,
        tool_name: str,
        arguments: JsonObject,
        idempotency_key: str,
        *,
        exact_replay: bool,
    ) -> None:
        allowed = tool_name in get_allowed_tool_names(run.status)
        in_immediate_successor = _IDEMPOTENT_REPLAY_STATES.get(tool_name) is run.status
        if not allowed and not in_immediate_successor:
            raise InvalidStateTransitionError(
                f"tool {tool_name} is not allowed while run is {run.status.value}"
            )

        intent_matches = False
        if tool_name in _STATEFUL_TOOLS:
            snapshot = self.database.get_snapshot(run.run_id, f"tool_args:{tool_name}")
            if snapshot is not None:
                if snapshot.payload.get("sha256") != idempotency_key:
                    raise IdempotencyConflictError(
                        "a different argument set was already accepted for this tool and run"
                    )
                intent_matches = True

        recovery_allowed = in_immediate_successor and (exact_replay or intent_matches)
        if not allowed and not recovery_allowed:
            raise InvalidStateTransitionError(
                f"tool {tool_name} is not allowed while run is {run.status.value}"
            )

    def _dispatch(self, tool_name: str, arguments: JsonObject) -> _Outcome:
        handlers = {
            "inspect_dataset": self._inspect_dataset,
            "build_graph": self._build_graph,
            "compute_graph_features": self._compute_graph_features,
            "cluster_network": self._cluster_network,
            "assign_roles": self._assign_roles,
            "rank_targets": self._rank_targets,
            "get_node_evidence": self._get_node_evidence,
            "create_review_case": self._create_review_case,
            "export_results": self._export_results,
            "verify_run": self._verify_run,
        }
        return handlers[tool_name](arguments)

    def _persist_result(
        self,
        tool_name: str,
        arguments: JsonObject,
        idempotency_key: str,
        envelope: JsonObject,
    ) -> None:
        run_id = arguments["run_id"]
        if tool_name in _STATEFUL_TOOLS:
            self._persist_tool_intent(tool_name, arguments)
        if tool_name in _ATOMIC_TRANSITIONS:
            expected, target = _ATOMIC_TRANSITIONS[tool_name]
            self.database.complete_tool(
                run_id,
                tool_name,
                envelope,
                expected_state=expected,
                target_state=target,
                idempotency_key=idempotency_key,
            )
        else:
            self.database.put_tool_result(
                run_id,
                tool_name,
                envelope,
                idempotency_key=idempotency_key,
            )

    def _persist_tool_intent(self, tool_name: str, arguments: JsonObject) -> None:
        """Persist immutable, hash-bound arguments before a non-atomic mutation."""

        self.database.put_snapshot(
            arguments["run_id"],
            f"tool_args:{tool_name}",
            {"sha256": _argument_hash(arguments), "arguments": arguments},
        )

    def _inspect_dataset(self, arguments: JsonObject) -> _Outcome:
        run = self.database.require_run(arguments["run_id"])
        dataset = self._load_registered_dataset(run.dataset_id)
        self._cache[run.run_id] = _RunCache(dataset=dataset)
        self.database.update_run(run.run_id, dataset_sha256=dataset.sha256)

        start = pd.Timestamp(dataset.transactions["date"].min()).date().isoformat()
        end = pd.Timestamp(dataset.transactions["date"].max()).date().isoformat()
        limitations = [
            "Seed inflows may be incomplete inside the supplied traversal.",
            "Depth-4 nodes may be truncated by the four-hop collection boundary.",
            "Transaction dates do not contain intraday ordering.",
            "The dataset contains no identity attributes or ground-truth labels.",
        ]
        data = {
            "dataset_sha256": dataset.sha256,
            "n_nodes": int(len(dataset.nodes)),
            "n_edges": int(len(dataset.edges)),
            "n_transactions": int(len(dataset.transactions)),
            "n_seed": int(dataset.nodes["is_seed"].sum()),
            "turnover_kzt": float(dataset.edges["sum_kzt"].sum()),
            "period": {"start": start, "end": end},
            "limitations": limitations,
        }
        return _Outcome(
            summary=(
                f"Dataset is valid: {data['n_nodes']:,} nodes, {data['n_edges']:,} edges, "
                f"{data['n_transactions']:,} transactions."
            ),
            data=data,
            warnings=tuple(limitations),
        )

    def _build_graph(self, arguments: JsonObject) -> _Outcome:
        cache = self._ensure_cache(arguments["run_id"], "dataset")
        cache.graph = build_directed_graph(cache.dataset.nodes, cache.dataset.edges)
        degree = dict(cache.graph.degree())
        orphan_count = sum(value == 0 for value in degree.values())
        edge_nodes = {int(value) for value in cache.dataset.edges["src"]} | {
            int(value) for value in cache.dataset.edges["dst"]
        }
        components = (
            nx.number_weakly_connected_components(cache.graph.subgraph(edge_nodes))
            if edge_nodes
            else 0
        )
        out_degree = dict(cache.graph.out_degree())
        truncated = sum(
            int(row.depth) == 4 and out_degree[int(row.gid)] == 0
            for row in cache.dataset.nodes.itertuples(index=False)
        )
        data = {
            "n_nodes": cache.graph.number_of_nodes(),
            "n_edges": cache.graph.number_of_edges(),
            "n_components_with_edges": int(components),
            "n_orphan_nodes": int(orphan_count),
            "n_truncated_depth4": int(truncated),
        }
        return _Outcome(
            summary=(
                f"Directed graph built with {data['n_nodes']:,} nodes and "
                f"{data['n_edges']:,} edges; {data['n_truncated_depth4']:,} "
                "depth-boundary nodes flagged."
            ),
            data=data,
        )

    def _compute_graph_features(self, arguments: JsonObject) -> _Outcome:
        cache = self._ensure_cache(arguments["run_id"], "graph")
        cache.temporal_enabled = bool(arguments["include_temporal"])
        cache.features = compute_node_features(cache.dataset, cache.graph)
        if not cache.temporal_enabled:
            cache.features = _without_temporal_signal(cache.features)
        warning_counts: Counter[str] = Counter()
        for flags in cache.features["uncertainty_flags"]:
            warning_counts.update(flags)
        data = {
            "feature_version": "v1",
            "computed_fields": [str(column) for column in cache.features.columns],
            "temporal_enabled": cache.temporal_enabled,
            "warning_counts": dict(sorted(warning_counts.items())),
        }
        return _Outcome(
            summary=f"Computed {len(data['computed_fields'])} deterministic feature fields.",
            data=data,
        )

    def _cluster_network(self, arguments: JsonObject) -> _Outcome:
        cache = self._ensure_cache(arguments["run_id"], "features")
        cache.resolution = float(arguments["resolution"])
        cache.random_seed = int(arguments["random_seed"])
        cache.cluster_assignments = cluster_network(
            cache.dataset.nodes,
            cache.dataset.edges,
            resolution=cache.resolution,
            random_seed=cache.random_seed,
        )
        joined = cache.cluster_assignments.merge(
            cache.dataset.nodes.loc[:, ["gid", "is_seed"]],
            on="gid",
            validate="one_to_one",
        )
        seeds_per_cluster = joined.groupby("cluster_id", sort=True)["is_seed"].sum()
        data = {
            "algorithm": "louvain",
            "resolution": cache.resolution,
            "random_seed": cache.random_seed,
            "n_clusters": int(cache.cluster_assignments["cluster_id"].nunique()),
            "n_multi_seed_clusters": int((seeds_per_cluster > 1).sum()),
        }
        return _Outcome(
            summary=(f"Assigned every node to {data['n_clusters']:,} reproducible communities."),
            data=data,
        )

    def _assign_roles(self, arguments: JsonObject) -> _Outcome:
        if arguments["ruleset_version"] != RULESET_VERSION:
            raise _ToolFailure("INVALID_ARGUMENT", "Unsupported ruleset version.")
        cache = self._ensure_cache(arguments["run_id"], "clusters")
        cache.role_assessments = assign_roles(cache.features, cache.cluster_assignments)
        role_counts = cache.role_assessments["role"].value_counts().sort_index()
        uncertain_count = int(
            cache.features["uncertainty_flags"].map(lambda flags: bool(flags)).sum()
        )
        data = {
            "ruleset_version": RULESET_VERSION,
            "assigned_count": int(len(cache.role_assessments)),
            "role_counts": {str(role): int(count) for role, count in role_counts.items()},
            "uncertain_count": uncertain_count,
        }
        return _Outcome(
            summary=f"Assigned one explainable role to {data['assigned_count']:,} nodes.",
            data=data,
        )

    def _rank_targets(self, arguments: JsonObject) -> _Outcome:
        cache = self._ensure_cache(arguments["run_id"], "roles")
        cache.rank_limit = int(arguments["limit"])
        cache.assessments, cache.top_nodes = score_and_rank(
            cache.features,
            cache.role_assessments,
            top_n=cache.rank_limit,
        )
        cache.clusters = summarize_clusters(
            cache.features,
            cache.assessments,
            cache.dataset.edges,
        )
        scores = cache.top_nodes["priority_score"]
        data = {
            "limit": cache.rank_limit,
            "ranked_count": int(len(cache.top_nodes)),
            "target_gids": [str(int(gid)) for gid in cache.top_nodes["gid"]],
            "score_range": {
                "minimum": float(scores.min()) if len(scores) else None,
                "maximum": float(scores.max()) if len(scores) else None,
            },
        }
        return _Outcome(
            summary=f"Ranked {data['ranked_count']} targets for analyst review.",
            data=data,
        )

    def _get_node_evidence(self, arguments: JsonObject) -> _Outcome:
        cache = self._ensure_cache(arguments["run_id"], "roles")
        gid = int(arguments["gid"])
        if gid not in cache.graph:
            raise _ToolFailure("INVALID_ARGUMENT", "GID is not present in this run.")
        feature_rows = cache.features.loc[cache.features["gid"] == gid]
        role_rows = cache.role_assessments.loc[cache.role_assessments["gid"] == gid]
        if len(feature_rows) != 1 or len(role_rows) != 1:
            raise _ToolFailure("ANALYTICS_FAILED", "Node evidence is incomplete.")
        feature = feature_rows.iloc[0]
        role = role_rows.iloc[0]
        cluster_id = int(role["cluster_id"])
        cluster_gids = cache.cluster_assignments.loc[
            cache.cluster_assignments["cluster_id"] == cluster_id, "gid"
        ]
        cluster_features = cache.dataset.nodes.loc[cache.dataset.nodes["gid"].isin(cluster_gids)]
        cluster_roles = cache.role_assessments.loc[
            cache.role_assessments["cluster_id"] == cluster_id, "role"
        ].value_counts()
        incoming, outgoing = _bounded_edges(
            cache.graph,
            gid,
            int(arguments["neighbor_hops"]),
        )
        node = {
            "gid": str(gid),
            "depth": int(feature["depth"]),
            "is_seed": bool(feature["is_seed"]),
            "in_degree": int(feature["in_degree"]),
            "out_degree": int(feature["out_degree"]),
            "in_kzt": float(feature["in_kzt"]),
            "out_kzt": float(feature["out_kzt"]),
            "pass_through_ratio": _finite_or_none(feature["pass_through_ratio"]),
            "pagerank": float(feature["pagerank"]),
            "betweenness": float(feature["betweenness"]),
            "seed_reach_count": int(feature["seed_reach_count"]),
            "rapid_outflow_ratio": _finite_or_none(feature["rapid_outflow_ratio"]),
            "truncated_by_depth": bool(feature["truncated_by_depth"]),
            "role": str(role["role"]),
            "role_score": float(role["role_score"]),
            "cluster_id": cluster_id,
            "evidence": str(role["evidence"]),
        }
        data = {
            "node": node,
            "incoming_edges": incoming,
            "outgoing_edges": outgoing,
            "cluster": {
                "cluster_id": cluster_id,
                "n_nodes": int(len(cluster_gids)),
                "n_seed": int(cluster_features["is_seed"].sum()),
                "role_counts": {
                    str(name): int(value) for name, value in cluster_roles.sort_index().items()
                },
            },
            "uncertainty_flags": [str(flag) for flag in feature["uncertainty_flags"]],
        }
        return _Outcome(
            summary=f"Returned calculated evidence for GID {gid}.",
            data=data,
        )

    def _create_review_case(self, arguments: JsonObject) -> _Outcome:
        cache = self._ensure_cache(arguments["run_id"], "ranking")
        target_gids = list(arguments["target_gids"])
        ranked_gids = [str(int(gid)) for gid in cache.top_nodes["gid"]]
        if target_gids != ranked_gids:
            raise _ToolFailure(
                "INVALID_ARGUMENT",
                "target_gids must exactly equal the complete persisted ranking.",
            )
        self._persist_tool_intent("create_review_case", arguments)
        try:
            created = self.database.create_review_case(
                arguments["run_id"],
                arguments["title"],
                target_gids,
            )
        except StorageError as exc:
            if isinstance(exc, (InvalidStateTransitionError, IdempotencyConflictError)):
                raise
            raise _ToolFailure(
                "CASE_WRITE_FAILED",
                "The local review case could not be persisted.",
                retriable=True,
            ) from exc
        data = {
            "case_id": created.case.case_id,
            "status": created.case.status.value,
            "target_count": len(created.case.target_gids),
            "idempotent_replay": created.idempotent_replay,
        }
        return _Outcome(
            summary=(
                f"Created review case {created.case.case_id} with {data['target_count']} targets."
            ),
            data=data,
        )

    def _export_results(self, arguments: JsonObject) -> _Outcome:
        cache = self._ensure_cache(arguments["run_id"], "ranking")
        run_id = arguments["run_id"]
        review_case = self.database.get_review_case_for_run(run_id)
        if review_case is None:
            raise _ToolFailure("EXPORT_FAILED", "A review case is required before export.")
        self._persist_tool_intent("export_results", arguments)
        try:
            run = self.database.require_run(run_id)
            audit_payload = {
                "schema_version": "v1",
                "run_id": run_id,
                "dataset_id": run.dataset_id,
                "dataset_sha256": cache.dataset.sha256,
                "ruleset_version": RULESET_VERSION,
                "case_id": review_case.case_id,
                "target_gids": list(review_case.target_gids),
                "node_count": int(len(cache.assessments)),
                "cluster_count": int(len(cache.clusters)),
            }
            stored = [
                self.artifact_store.write_csv(
                    run_id,
                    "nodes_roles.csv",
                    _NODES_HEADERS,
                    _frame_records(cache.assessments, _NODES_HEADERS, gid_fields={"gid"}),
                ),
                self.artifact_store.write_csv(
                    run_id,
                    "clusters.csv",
                    _CLUSTERS_HEADERS,
                    _frame_records(cache.clusters, _CLUSTERS_HEADERS),
                ),
                self.artifact_store.write_csv(
                    run_id,
                    "top_nodes.csv",
                    _TOP_HEADERS,
                    _frame_records(cache.top_nodes, _TOP_HEADERS, gid_fields={"gid"}),
                ),
                self.artifact_store.write_bytes(
                    run_id,
                    REPORT_ARTIFACT_NAME,
                    build_review_workbook(
                        cache.assessments,
                        cache.clusters,
                        cache.top_nodes,
                        audit_payload,
                    ),
                ),
            ]
            if arguments["include_audit"]:
                stored.append(
                    self.artifact_store.write_json(
                        run_id,
                        audit_payload,
                    )
                )
            for artifact in stored:
                self.database.register_artifact(
                    run_id,
                    name=artifact.name,
                    relative_path=artifact.relative_path,
                    sha256=artifact.sha256,
                    row_count=artifact.row_count,
                    size_bytes=artifact.size_bytes,
                )
            self.database.mark_exported(
                run_id,
                include_audit=bool(arguments["include_audit"]),
            )
        except (ArtifactStoreError, OSError, StorageError) as exc:
            if isinstance(exc, (InvalidStateTransitionError, IdempotencyConflictError)):
                raise
            raise _ToolFailure(
                "EXPORT_FAILED",
                "The controlled export bundle could not be written.",
                retriable=True,
            ) from exc

        data = {
            "artifact_names": [artifact.name for artifact in stored],
            "sha256_by_name": {artifact.name: artifact.sha256 for artifact in stored},
            "row_count_by_name": {artifact.name: artifact.row_count for artifact in stored},
        }
        return _Outcome(
            summary=f"Exported and registered {len(stored)} controlled artifacts.",
            data=data,
            artifacts=tuple(artifact.name for artifact in stored),
        )

    def _verify_run(self, arguments: JsonObject) -> _Outcome:
        run_id = arguments["run_id"]
        report = self._independent_verification(run_id)
        passed = not report["failed_checks"]
        report["passed"] = passed
        report["completed_at"] = datetime.now(UTC).isoformat()
        self._persist_tool_intent("verify_run", arguments)
        self.database.record_verification(run_id, passed=passed, checks=report)
        if passed:
            return _Outcome(
                summary=f"Verification passed: {len(report['checks'])} checks completed.",
                data=report,
            )
        return _Outcome(
            summary=f"Verification failed: {len(report['failed_checks'])} checks failed.",
            data=report,
            ok=False,
            error_code="VERIFICATION_FAILED",
            error_message="Independent verification found invalid or inconsistent output.",
            error_details={"failed_checks": report["failed_checks"]},
        )

    def _independent_verification(self, run_id: str) -> JsonObject:
        run = self.database.require_run(run_id)
        dataset = self._load_registered_dataset(run.dataset_id)
        checks: list[JsonObject] = []

        def record(name: str, passed: bool, detail: str) -> None:
            checks.append({"name": name, "passed": bool(passed), "detail": detail[:240]})

        record(
            "run_state",
            run.status in {RunState.EXPORTED, RunState.VERIFIED},
            f"expected exported (or verified recovery), observed {run.status.value}",
        )
        record(
            "dataset_fingerprint",
            run.dataset_sha256 == dataset.sha256,
            "persisted fingerprint matches registered input",
        )
        record(
            "ruleset_version",
            run.ruleset_version == RULESET_VERSION,
            "persisted run uses the supported immutable ruleset",
        )
        canonical: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None = None
        try:
            canonical = self._recompute_authoritative_results(run_id, dataset)
            record(
                "authoritative_recomputation",
                True,
                "analytics were independently recomputed from the registered dataset",
            )
        except Exception:
            record(
                "authoritative_recomputation",
                False,
                "persisted tool arguments or deterministic recomputation are invalid",
            )

        records = {artifact.name: artifact for artifact in self.database.list_artifacts(run_id)}
        record(
            "mandatory_artifacts_registered",
            set(MANDATORY_ARTIFACT_NAMES | READER_ARTIFACT_NAMES).issubset(records),
            "all three mandatory CSV records and the Excel report are registered",
        )

        parsed: dict[str, tuple[tuple[str, ...], list[dict[str, str]]]] = {}
        for name in sorted(MANDATORY_ARTIFACT_NAMES):
            artifact = records.get(name)
            if artifact is None:
                record(f"{name}:integrity", False, "artifact record is missing")
                continue
            try:
                raw = self.artifact_store.read_bytes(run_id, name)
                digest_matches = hashlib.sha256(raw).hexdigest() == artifact.sha256
                size_matches = len(raw) == artifact.size_bytes
                headers, rows = _read_csv(raw)
                row_count_matches = len(rows) == artifact.row_count
                parsed[name] = (headers, rows)
                record(
                    f"{name}:integrity",
                    digest_matches and size_matches and row_count_matches,
                    "hash, size, and row-count metadata match bytes",
                )
            except Exception:
                record(f"{name}:integrity", False, "artifact bytes are missing or malformed")

        audit = records.get("audit.json")
        if audit is not None:
            try:
                audit_raw = self.artifact_store.read_bytes(run_id, "audit.json")
                audit_payload = json.loads(audit_raw)
                audit_valid = (
                    hashlib.sha256(audit_raw).hexdigest() == audit.sha256
                    and len(audit_raw) == audit.size_bytes
                    and audit.row_count is None
                    and isinstance(audit_payload, dict)
                    and audit_payload.get("run_id") == run_id
                    and audit_payload.get("dataset_sha256") == dataset.sha256
                )
                record(
                    "audit.json:integrity",
                    audit_valid,
                    "optional audit hash and immutable run identity match",
                )
            except Exception:
                record("audit.json:integrity", False, "optional audit is malformed")

        report = records.get(REPORT_ARTIFACT_NAME)
        if report is not None:
            try:
                report_raw = self.artifact_store.read_bytes(run_id, REPORT_ARTIFACT_NAME)
                report_valid = (
                    hashlib.sha256(report_raw).hexdigest() == report.sha256
                    and len(report_raw) == report.size_bytes
                    and report.row_count is None
                    and _xlsx_sheet_names(report_raw) == REPORT_SHEET_NAMES
                )
                record(
                    f"{REPORT_ARTIFACT_NAME}:integrity",
                    report_valid,
                    "Excel hash, size, package structure, and sheet names match",
                )
            except Exception:
                record(
                    f"{REPORT_ARTIFACT_NAME}:integrity",
                    False,
                    "Excel review report is malformed",
                )
        else:
            record(
                f"{REPORT_ARTIFACT_NAME}:integrity",
                False,
                "Excel review report is missing",
            )

        node_rows: list[dict[str, str]] = []
        cluster_rows: list[dict[str, str]] = []
        top_rows: list[dict[str, str]] = []
        if "nodes_roles.csv" in parsed:
            headers, node_rows = parsed["nodes_roles.csv"]
            record(
                "nodes_roles_schema",
                headers == _NODES_HEADERS,
                "nodes_roles.csv uses the required exact columns",
            )
        if "clusters.csv" in parsed:
            headers, cluster_rows = parsed["clusters.csv"]
            record(
                "clusters_schema",
                headers == _CLUSTERS_HEADERS,
                "clusters.csv uses the required exact columns",
            )
        if "top_nodes.csv" in parsed:
            headers, top_rows = parsed["top_nodes.csv"]
            record(
                "top_nodes_schema",
                headers == _TOP_HEADERS,
                "top_nodes.csv uses the required exact columns",
            )

        node_valid, node_index, cluster_members = _validate_node_rows(
            node_rows,
            {str(int(gid)) for gid in dataset.nodes["gid"]},
        )
        record("all_nodes", node_valid, "every input GID has one bounded assessment")

        cluster_valid = _validate_cluster_rows(cluster_rows, cluster_members)
        record(
            "cluster_coverage",
            cluster_valid,
            "cluster summaries match node assignments and counts",
        )

        top_valid, top_gids = _validate_top_rows(top_rows, node_index)
        record(
            "ranking",
            top_valid,
            "ranking has at least 20 ordered unique targets and matches assessments",
        )

        authoritative_top_gids: list[str] = []
        if canonical is None:
            nodes_authoritative = False
            clusters_authoritative = False
            top_authoritative = False
        else:
            expected_nodes, expected_clusters, expected_top = canonical
            nodes_authoritative = _nodes_match_authoritative(node_rows, expected_nodes)
            clusters_authoritative = _clusters_match_authoritative(
                cluster_rows,
                expected_clusters,
            )
            top_authoritative = _top_matches_authoritative(top_rows, expected_top)
            authoritative_top_gids = [str(int(gid)) for gid in expected_top["gid"]]
        record(
            "nodes_roles_authoritative",
            nodes_authoritative,
            "roles, scores, clusters, and evidence match independent recomputation",
        )
        record(
            "clusters_authoritative",
            clusters_authoritative,
            "cluster rows match independent recomputation",
        )
        record(
            "top_nodes_authoritative",
            top_authoritative,
            "the complete ranked list matches independent recomputation",
        )

        review_case = self.database.get_review_case_for_run(run_id)
        case_valid = (
            review_case is not None
            and list(review_case.target_gids) == top_gids
            and list(review_case.target_gids) == authoritative_top_gids
            and len(review_case.target_gids) >= 20
        )
        record(
            "review_case_targets",
            case_valid,
            "review case exactly equals the complete authoritative ranking",
        )

        failed = [check["name"] for check in checks if not check["passed"]]
        return {"checks": checks, "failed_checks": failed}

    def _recompute_authoritative_results(
        self,
        run_id: str,
        dataset: DatasetTables,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Recompute export frames without consulting cache or exported output."""

        self._verified_stored_arguments(run_id, "inspect_dataset")
        self._verified_stored_arguments(run_id, "build_graph")
        feature_args = self._verified_stored_arguments(run_id, "compute_graph_features")
        cluster_args = self._verified_stored_arguments(run_id, "cluster_network")
        role_args = self._verified_stored_arguments(run_id, "assign_roles")
        rank_args = self._verified_stored_arguments(run_id, "rank_targets")
        if role_args["ruleset_version"] != RULESET_VERSION:
            raise ValueError("unsupported persisted ruleset")

        graph = build_directed_graph(dataset.nodes, dataset.edges)
        features = compute_node_features(dataset, graph)
        if not bool(feature_args["include_temporal"]):
            features = _without_temporal_signal(features)
        assignments = cluster_network(
            dataset.nodes,
            dataset.edges,
            resolution=float(cluster_args["resolution"]),
            random_seed=int(cluster_args["random_seed"]),
        )
        roles = assign_roles(features, assignments)
        assessments, top_nodes = score_and_rank(
            features,
            roles,
            top_n=int(rank_args["limit"]),
        )
        clusters = summarize_clusters(features, assessments, dataset.edges)
        return assessments, clusters, top_nodes

    def _verified_stored_arguments(self, run_id: str, tool_name: str) -> JsonObject:
        snapshot = self.database.get_snapshot(run_id, f"tool_args:{tool_name}")
        if snapshot is None:
            raise ValueError(f"missing persisted arguments for {tool_name}")
        arguments = snapshot.payload.get("arguments")
        digest = snapshot.payload.get("sha256")
        if not isinstance(arguments, Mapping) or digest != _argument_hash(arguments):
            raise ValueError(f"invalid persisted arguments for {tool_name}")
        validated = validate_tool_arguments(tool_name, dict(arguments))
        if str(UUID(validated["run_id"])) != run_id:
            raise ValueError(f"persisted run_id mismatch for {tool_name}")
        return validated

    def _ensure_cache(self, run_id: str, stage: str) -> _RunCache:
        stage_order = {
            "dataset": 0,
            "graph": 1,
            "features": 2,
            "clusters": 3,
            "roles": 4,
            "ranking": 5,
        }
        target = stage_order[stage]
        run = self.database.require_run(run_id)
        cache = self._cache.get(run_id)
        if cache is None:
            cache = _RunCache(dataset=self._load_registered_dataset(run.dataset_id))
            self._cache[run_id] = cache
        if run.dataset_sha256 is not None and cache.dataset.sha256 != run.dataset_sha256:
            raise _ToolFailure(
                "DATASET_INCONSISTENT",
                "Registered dataset fingerprint changed after inspection.",
            )
        if target >= 1 and cache.graph is None:
            cache.graph = build_directed_graph(cache.dataset.nodes, cache.dataset.edges)
        if target >= 2 and cache.features is None:
            feature_args = self._stored_arguments(
                run_id,
                "compute_graph_features",
                {"include_temporal": True},
            )
            cache.temporal_enabled = bool(feature_args["include_temporal"])
            cache.features = compute_node_features(cache.dataset, cache.graph)
            if not cache.temporal_enabled:
                cache.features = _without_temporal_signal(cache.features)
        if target >= 3 and cache.cluster_assignments is None:
            cluster_args = self._stored_arguments(
                run_id,
                "cluster_network",
                {"resolution": 1.0, "random_seed": 42},
            )
            cache.resolution = float(cluster_args["resolution"])
            cache.random_seed = int(cluster_args["random_seed"])
            cache.cluster_assignments = cluster_network(
                cache.dataset.nodes,
                cache.dataset.edges,
                resolution=cache.resolution,
                random_seed=cache.random_seed,
            )
        if target >= 4 and cache.role_assessments is None:
            cache.role_assessments = assign_roles(cache.features, cache.cluster_assignments)
        if target >= 5 and cache.top_nodes is None:
            rank_args = self._stored_arguments(run_id, "rank_targets", {"limit": 20})
            cache.rank_limit = int(rank_args["limit"])
            cache.assessments, cache.top_nodes = score_and_rank(
                cache.features,
                cache.role_assessments,
                top_n=cache.rank_limit,
            )
            cache.clusters = summarize_clusters(
                cache.features,
                cache.assessments,
                cache.dataset.edges,
            )
        return cache

    def _stored_arguments(
        self,
        run_id: str,
        tool_name: str,
        defaults: Mapping[str, Any],
    ) -> JsonObject:
        snapshot = self.database.get_snapshot(run_id, f"tool_args:{tool_name}")
        if snapshot is None:
            return dict(defaults)
        arguments = snapshot.payload.get("arguments", {})
        return {**defaults, **{key: value for key, value in arguments.items() if key != "run_id"}}

    def _load_registered_dataset(self, dataset_id: str) -> DatasetTables:
        path = self._dataset_registry.get(dataset_id)
        if path is None or not path.is_dir():
            raise _ToolFailure("DATASET_NOT_FOUND", "Registered dataset is unavailable.")
        expected_seeds = self._expected_seed_counts.get(dataset_id)
        expected_period = self._expected_periods.get(dataset_id)
        try:
            return load_dataset(
                path,
                expected_seed_count=expected_seeds,
                expected_period=expected_period,
            )
        except DatasetValidationError as exc:
            text = str(exc).lower()
            code = (
                "DATASET_INCONSISTENT"
                if any(term in text for term in ("reconcile", "different pairs", "reference gids"))
                else "DATASET_SCHEMA_INVALID"
            )
            raise _ToolFailure(code, "Registered dataset failed deterministic validation.") from exc

    @staticmethod
    def _outcome_envelope(tool_name: str, run_id: str, outcome: _Outcome) -> JsonObject:
        error = None
        if not outcome.ok:
            error = {
                "code": outcome.error_code,
                "message": outcome.error_message,
                "retriable": outcome.error_code in _RETRIABLE_ERROR_CODES,
                "details": _json_safe(outcome.error_details),
            }
        return {
            "ok": outcome.ok,
            "tool": tool_name,
            "run_id": run_id,
            "summary": outcome.summary,
            "data": _json_safe(outcome.data),
            "warnings": list(outcome.warnings),
            "artifacts": list(outcome.artifacts),
            "error": error,
        }

    @staticmethod
    def _error_envelope(
        tool_name: str,
        run_id: str | None,
        code: str,
        message: str,
        *,
        retriable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> JsonObject:
        return {
            "ok": False,
            "tool": tool_name,
            "run_id": run_id,
            "summary": f"{tool_name} was rejected or failed safely.",
            "data": {},
            "warnings": [],
            "artifacts": [],
            "error": {
                "code": code,
                "message": message,
                "retriable": retriable,
                "details": _json_safe(dict(details or {})),
            },
        }

    @staticmethod
    def _normalize_exception(exc: Exception) -> _ToolFailure:
        if isinstance(exc, _ToolFailure):
            return exc
        if isinstance(exc, InvalidStateTransitionError):
            return _ToolFailure("INVALID_STATE", "Tool is not allowed in the persisted run state.")
        if isinstance(exc, IdempotencyConflictError):
            return _ToolFailure(
                "INVALID_ARGUMENT",
                "A different argument set was already accepted for this tool and run.",
            )
        if isinstance(exc, RecordNotFoundError):
            return _ToolFailure("INVALID_ARGUMENT", "Run does not exist.")
        if isinstance(exc, StorageValidationError):
            return _ToolFailure("INVALID_ARGUMENT", "A persisted value failed validation.")
        if isinstance(exc, DatasetValidationError):
            return _ToolFailure(
                "DATASET_SCHEMA_INVALID",
                "Registered dataset failed deterministic validation.",
            )
        if isinstance(exc, (ValueError, nx.NetworkXException)):
            return _ToolFailure(
                "ANALYTICS_FAILED",
                "Deterministic analytics could not complete.",
                retriable=True,
            )
        if isinstance(exc, ArtifactStoreError):
            return _ToolFailure(
                "EXPORT_FAILED",
                "Controlled artifact storage failed.",
                retriable=True,
            )
        return _ToolFailure(
            "INTERNAL_ERROR",
            "An internal operation failed without exposing sensitive details.",
            retriable=True,
        )


def _logical_dataset_id(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value))


def _safe_run_id(arguments: Any) -> str | None:
    if isinstance(arguments, Mapping) and isinstance(arguments.get("run_id"), str):
        return arguments["run_id"]
    return None


def _argument_hash(arguments: Mapping[str, Any]) -> str:
    canonical = json.dumps(arguments, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _schema_error_field(error: SchemaValidationError) -> str:
    return ".".join(str(part) for part in error.absolute_path) or "$"


def _safe_event_arguments(arguments: Mapping[str, Any]) -> JsonObject:
    safe: JsonObject = {}
    for key, value in arguments.items():
        if key == "target_gids":
            safe["target_count"] = len(value)
        elif key == "title":
            safe["title_length"] = len(value)
        else:
            safe[key] = value
    return safe


def _without_temporal_signal(features: pd.DataFrame) -> pd.DataFrame:
    result = features.copy()
    result["rapid_outflow_ratio"] = np.nan

    def flags_without_temporal(flags: Sequence[str]) -> tuple[str, ...]:
        retained = [
            flag
            for flag in flags
            if flag not in {"date_only_resolution", "temporal_signal_unavailable"}
        ]
        retained.append("temporal_signal_disabled")
        return tuple(retained)

    result["uncertainty_flags"] = result["uncertainty_flags"].map(flags_without_temporal)
    return result


def _bounded_edges(
    graph: nx.DiGraph,
    gid: int,
    hops: int,
) -> tuple[list[JsonObject], list[JsonObject]]:
    incoming_nodes = _walk_nodes(graph, gid, hops, inbound=True)
    outgoing_nodes = _walk_nodes(graph, gid, hops, inbound=False)
    incoming = _edge_records(graph, incoming_nodes)
    outgoing = _edge_records(graph, outgoing_nodes)
    return incoming, outgoing


def _walk_nodes(graph: nx.DiGraph, gid: int, hops: int, *, inbound: bool) -> set[int]:
    visited = {gid}
    queue: deque[tuple[int, int]] = deque([(gid, 0)])
    while queue:
        node, distance = queue.popleft()
        if distance >= hops:
            continue
        neighbors = graph.predecessors(node) if inbound else graph.successors(node)
        for neighbor in sorted(int(value) for value in neighbors):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, distance + 1))
    return visited


def _edge_records(graph: nx.DiGraph, nodes: set[int]) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for src, dst, attributes in sorted(
        graph.subgraph(nodes).edges(data=True),
        key=lambda item: (int(item[0]), int(item[1])),
    ):
        rows.append(
            {
                "src": str(int(src)),
                "dst": str(int(dst)),
                "sum_kzt": float(attributes["sum_kzt"]),
                "n_tx": int(attributes["n_tx"]),
            }
        )
    return rows


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _frame_records(
    frame: pd.DataFrame,
    columns: Sequence[str],
    *,
    gid_fields: set[str] | None = None,
) -> list[JsonObject]:
    gid_fields = gid_fields or set()
    records: list[JsonObject] = []
    for row in frame.loc[:, list(columns)].itertuples(index=False, name=None):
        record: JsonObject = {}
        for column, value in zip(columns, row, strict=True):
            if column in gid_fields:
                record[column] = str(int(value))
            elif column == "top_gids" and not isinstance(value, str):
                record[column] = json.dumps(
                    [str(item) for item in value],
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            else:
                record[column] = _json_safe(value)
        records.append(record)
    return records


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if pd.isna(value):
        return None
    return str(value)


def _read_csv(raw: bytes) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise ValueError("CSV header is missing")
    rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError("CSV contains fields beyond its header")
    return tuple(reader.fieldnames), rows


def _xlsx_sheet_names(raw: bytes) -> tuple[str, ...]:
    """Read workbook sheet names without trusting a spreadsheet engine."""

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if "xl/workbook.xml" not in archive.namelist():
            raise ValueError("XLSX workbook part is missing")
        root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    return tuple(
        sheet.attrib["name"]
        for sheet in root.findall(f"{namespace}sheets/{namespace}sheet")
    )


def _validate_node_rows(
    rows: list[dict[str, str]],
    expected_gids: set[str],
) -> tuple[bool, dict[str, dict[str, Any]], dict[int, set[str]]]:
    index: dict[str, dict[str, Any]] = {}
    cluster_members: dict[int, set[str]] = {}
    valid = bool(rows)
    for row in rows:
        try:
            gid = row["gid"]
            role = row["role"]
            role_score = float(row["role_score"])
            priority_score = float(row["priority_score"])
            cluster_id = int(row["cluster_id"])
            evidence = row["evidence"]
            row_valid = (
                bool(_GID.fullmatch(gid))
                and gid not in index
                and role in ROLES
                and math.isfinite(role_score)
                and 0.0 <= role_score <= 1.0
                and math.isfinite(priority_score)
                and 0.0 <= priority_score <= 1.0
                and 1 <= len(evidence) <= 200
                and bool(_NUMBER.search(evidence))
            )
            valid = valid and row_valid
            if row_valid:
                index[gid] = {
                    "role": role,
                    "role_score": role_score,
                    "priority_score": priority_score,
                    "cluster_id": cluster_id,
                }
                cluster_members.setdefault(cluster_id, set()).add(gid)
        except (KeyError, TypeError, ValueError, OverflowError):
            valid = False
    valid = valid and set(index) == expected_gids and len(rows) == len(expected_gids)
    return valid, index, cluster_members


def _validate_cluster_rows(
    rows: list[dict[str, str]],
    cluster_members: Mapping[int, set[str]],
) -> bool:
    seen: set[int] = set()
    valid = bool(rows) and bool(cluster_members)
    for row in rows:
        try:
            cluster_id = int(row["cluster_id"])
            n_nodes = int(row["n_nodes"])
            n_seed = int(row["n_seed"])
            amount = float(row["sum_kzt_internal"])
            top_gids = json.loads(row["top_gids"])
            hypothesis = row["hypothesis"]
            row_valid = (
                cluster_id not in seen
                and cluster_id in cluster_members
                and n_nodes == len(cluster_members[cluster_id])
                and n_nodes > 0
                and 0 <= n_seed <= n_nodes
                and math.isfinite(amount)
                and amount >= 0.0
                and isinstance(top_gids, list)
                and all(
                    isinstance(gid, str)
                    and gid in cluster_members[cluster_id]
                    and _GID.fullmatch(gid)
                    for gid in top_gids
                )
                and bool(hypothesis.strip())
            )
            valid = valid and row_valid
            seen.add(cluster_id)
        except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError):
            valid = False
    return valid and seen == set(cluster_members)


def _validate_top_rows(
    rows: list[dict[str, str]],
    node_index: Mapping[str, Mapping[str, Any]],
) -> tuple[bool, list[str]]:
    parsed: list[tuple[int, str, str, float]] = []
    valid = len(rows) >= 20
    seen: set[str] = set()
    for row in rows:
        try:
            rank = int(row["rank"])
            gid = row["gid"]
            role = row["role"]
            score = float(row["priority_score"])
            why = row["why"]
            node = node_index[gid]
            row_valid = (
                bool(_GID.fullmatch(gid))
                and gid not in seen
                and role == node["role"]
                and math.isfinite(score)
                and 0.0 <= score <= 1.0
                and math.isclose(score, float(node["priority_score"]), abs_tol=1e-6)
                and 1 <= len(why) <= 200
                and bool(_NUMBER.search(why))
            )
            valid = valid and row_valid
            seen.add(gid)
            parsed.append((rank, gid, role, score))
        except (KeyError, TypeError, ValueError, OverflowError):
            valid = False
    valid = valid and [item[0] for item in parsed] == list(range(1, len(parsed) + 1))
    expected = sorted(
        node_index,
        key=lambda gid: (
            -float(node_index[gid]["priority_score"]),
            -float(node_index[gid]["role_score"]),
            int(gid),
        ),
    )[: len(parsed)]
    gids = [item[1] for item in parsed]
    valid = valid and gids == expected
    return valid, gids


def _numbers_equal(left: object, right: object) -> bool:
    try:
        left_number = float(left)
        right_number = float(right)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(left_number)
        and math.isfinite(right_number)
        and math.isclose(left_number, right_number, rel_tol=0.0, abs_tol=1e-6)
    )


def _nodes_match_authoritative(
    rows: Sequence[Mapping[str, str]],
    expected: pd.DataFrame,
) -> bool:
    canonical = expected.sort_values("gid", kind="mergesort")
    if len(rows) != len(canonical):
        return False
    for actual, row in zip(rows, canonical.itertuples(index=False), strict=True):
        try:
            if not (
                actual["gid"] == str(int(row.gid))
                and actual["role"] == str(row.role)
                and _numbers_equal(actual["role_score"], row.role_score)
                and int(actual["cluster_id"]) == int(row.cluster_id)
                and _numbers_equal(actual["priority_score"], row.priority_score)
                and actual["evidence"] == str(row.evidence)
            ):
                return False
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
    return True


def _clusters_match_authoritative(
    rows: Sequence[Mapping[str, str]],
    expected: pd.DataFrame,
) -> bool:
    canonical = expected.sort_values("cluster_id", kind="mergesort")
    if len(rows) != len(canonical):
        return False
    for actual, row in zip(rows, canonical.itertuples(index=False), strict=True):
        try:
            top_gids = json.loads(actual["top_gids"])
            expected_top_gids = [str(gid) for gid in row.top_gids]
            if not (
                int(actual["cluster_id"]) == int(row.cluster_id)
                and int(actual["n_nodes"]) == int(row.n_nodes)
                and int(actual["n_seed"]) == int(row.n_seed)
                and _numbers_equal(actual["sum_kzt_internal"], row.sum_kzt_internal)
                and top_gids == expected_top_gids
                and actual["hypothesis"] == str(row.hypothesis)
            ):
                return False
        except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError):
            return False
    return True


def _top_matches_authoritative(
    rows: Sequence[Mapping[str, str]],
    expected: pd.DataFrame,
) -> bool:
    canonical = expected.sort_values("rank", kind="mergesort")
    if len(rows) != len(canonical):
        return False
    for actual, row in zip(rows, canonical.itertuples(index=False), strict=True):
        try:
            if not (
                int(actual["rank"]) == int(row.rank)
                and actual["gid"] == str(int(row.gid))
                and actual["role"] == str(row.role)
                and _numbers_equal(actual["priority_score"], row.priority_score)
                and actual["why"] == str(row.why)
            ):
                return False
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
    return True


def _tools_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aml-agent-tools",
        description="Run the deterministic AML Agent tool workflow without OpenAI.",
    )
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--database", type=Path, default=Path("var/aml-agent.sqlite3"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Remove only the selected SQLite database files before this run.",
    )
    return parser


def run_tools_cli(argv: Sequence[str] | None = None) -> JsonObject:
    """Execute the complete deterministic golden path and return a compact summary."""

    parser = _tools_parser()
    args = parser.parse_args(argv)
    if not 20 <= args.top <= 100:
        parser.error("--top must be between 20 and 100")
    if args.reset:
        _reset_database_files(args.database)

    with Database(args.database) as database:
        runtime = ToolRuntime(
            database,
            ArtifactStore(args.artifacts),
            {"cli-bundled": args.data},
            expected_periods={"cli-bundled": ("2026-07-01", "2026-07-31")},
        )
        run = runtime.create_run("cli-bundled", RunMode.DEMO)
        calls: list[tuple[str, JsonObject]] = [
            ("inspect_dataset", {"run_id": run.run_id}),
            ("build_graph", {"run_id": run.run_id}),
            (
                "compute_graph_features",
                {"run_id": run.run_id, "include_temporal": True},
            ),
            (
                "cluster_network",
                {"run_id": run.run_id, "resolution": 1.0, "random_seed": 42},
            ),
            ("assign_roles", {"run_id": run.run_id, "ruleset_version": RULESET_VERSION}),
            ("rank_targets", {"run_id": run.run_id, "limit": args.top}),
        ]
        steps: list[JsonObject] = []
        ranked: JsonObject | None = None
        for name, call_arguments in calls:
            result = runtime.execute(name, call_arguments)
            steps.append(_compact_step(result))
            if not result["ok"]:
                return _cli_summary(database, run.run_id, steps, result)
            if name == "rank_targets":
                ranked = result
        assert ranked is not None
        case_result = runtime.execute(
            "create_review_case",
            {
                "run_id": run.run_id,
                "target_gids": ranked["data"]["target_gids"],
                "title": "Priority structural indicators for analyst review",
            },
        )
        steps.append(_compact_step(case_result))
        if not case_result["ok"]:
            return _cli_summary(database, run.run_id, steps, case_result)
        for name, call_arguments in (
            (
                "export_results",
                {"run_id": run.run_id, "include_audit": True},
            ),
            ("verify_run", {"run_id": run.run_id}),
        ):
            result = runtime.execute(name, call_arguments)
            steps.append(_compact_step(result))
            if not result["ok"]:
                return _cli_summary(database, run.run_id, steps, result)
        return _cli_summary(database, run.run_id, steps, result)


def tools_cli_main(argv: Sequence[str] | None = None) -> None:
    """Installed ``aml-agent-tools`` entry point."""

    print(json.dumps(run_tools_cli(argv), ensure_ascii=False, indent=2))


def _compact_step(result: Mapping[str, Any]) -> JsonObject:
    return {
        "tool": result["tool"],
        "ok": result["ok"],
        "summary": result["summary"],
        **({"error": result["error"]} if result["error"] else {}),
    }


def _cli_summary(
    database: Database,
    run_id: str,
    steps: list[JsonObject],
    last_result: Mapping[str, Any],
) -> JsonObject:
    run = database.require_run(run_id)
    review_case = database.get_review_case_for_run(run_id)
    return {
        "status": run.status.value,
        "run_id": run_id,
        "case_id": review_case.case_id if review_case else None,
        "verification_status": run.verification_status.value,
        "ok": bool(last_result["ok"]),
        "steps": steps,
    }


def _reset_database_files(path: Path) -> None:
    resolved = path.expanduser().resolve(strict=False)
    if resolved.exists() and resolved.is_dir():
        raise ValueError("--database must identify a file, not a directory")
    if not resolved.name or resolved.parent == resolved:
        raise ValueError("refusing to reset an unsafe database path")
    for candidate in (resolved, Path(f"{resolved}-wal"), Path(f"{resolved}-shm")):
        candidate.unlink(missing_ok=True)


__all__ = ["ToolRuntime", "run_tools_cli", "tools_cli_main"]
