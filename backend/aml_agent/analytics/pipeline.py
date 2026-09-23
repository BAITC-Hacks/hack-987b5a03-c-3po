"""High-level deterministic AML analytics pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from pathlib import Path

import networkx as nx
import pandas as pd

from .clustering import cluster_network, summarize_clusters
from .exports import write_analytics_csvs
from .features import build_directed_graph, compute_node_features
from .roles import RULESET_VERSION, assign_roles, score_and_rank
from .validation import DatasetTables, load_dataset, validate_dataset


@dataclass(frozen=True, slots=True)
class AnalyticsResult:
    """In-memory authoritative result returned to tools and CLI adapters."""

    dataset_sha256: str
    ruleset_version: str
    features: pd.DataFrame
    assessments: pd.DataFrame
    clusters: pd.DataFrame
    top_nodes: pd.DataFrame
    graph: nx.DiGraph
    artifact_paths: dict[str, Path]


def analyze_dataset(
    data_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    top_n: int = 20,
    expected_seed_count: int | None = None,
    expected_period: tuple[str | Date, str | Date] | None = None,
) -> AnalyticsResult:
    """Load a parquet bundle and execute the complete v1 analytics pipeline."""

    dataset = load_dataset(
        data_dir,
        expected_seed_count=expected_seed_count,
        expected_period=expected_period,
    )
    return analyze_validated_dataset(dataset, output_dir=output_dir, top_n=top_n)


def analyze_frames(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    transactions: pd.DataFrame,
    *,
    output_dir: str | Path | None = None,
    top_n: int = 20,
    expected_seed_count: int | None = None,
    expected_period: tuple[str | Date, str | Date] | None = None,
) -> AnalyticsResult:
    """Validate in-memory tables and execute the same production pipeline."""

    dataset = validate_dataset(
        nodes,
        edges,
        transactions,
        expected_seed_count=expected_seed_count,
        expected_period=expected_period,
    )
    return analyze_validated_dataset(dataset, output_dir=output_dir, top_n=top_n)


def analyze_validated_dataset(
    dataset: DatasetTables,
    *,
    output_dir: str | Path | None = None,
    top_n: int = 20,
) -> AnalyticsResult:
    """Execute analytics for already validated tables without re-reading files."""

    if not 20 <= top_n <= 100:
        raise ValueError("top_n must be between 20 and 100")

    graph = build_directed_graph(dataset.nodes, dataset.edges)
    features = compute_node_features(dataset, graph)
    cluster_assignments = cluster_network(dataset.nodes, dataset.edges)
    role_assessments = assign_roles(features, cluster_assignments)
    assessments, top_nodes = score_and_rank(features, role_assessments, top_n=top_n)
    clusters = summarize_clusters(features, assessments, dataset.edges)

    artifact_paths: dict[str, Path] = {}
    if output_dir is not None:
        artifact_paths = write_analytics_csvs(output_dir, assessments, clusters, top_nodes)
    return AnalyticsResult(
        dataset_sha256=dataset.sha256,
        ruleset_version=RULESET_VERSION,
        features=features,
        assessments=assessments,
        clusters=clusters,
        top_nodes=top_nodes,
        graph=graph,
        artifact_paths=artifact_paths,
    )
