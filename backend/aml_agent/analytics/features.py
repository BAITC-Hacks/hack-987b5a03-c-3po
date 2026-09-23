"""Deterministic graph construction and node feature engineering."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

import networkx as nx
import numpy as np
import pandas as pd

from .validation import DatasetTables

BETWEENNESS_SAMPLE_SIZE: Final[int] = 300
RANDOM_SEED: Final[int] = 42


def build_directed_graph(nodes: pd.DataFrame, edges: pd.DataFrame) -> nx.DiGraph:
    """Build an amount-weighted directed graph including every input node."""

    graph = nx.DiGraph()
    for row in nodes.sort_values("gid", kind="mergesort").itertuples(index=False):
        graph.add_node(int(row.gid), depth=int(row.depth), is_seed=bool(row.is_seed))
    for row in edges.sort_values(["src", "dst"], kind="mergesort").itertuples(index=False):
        graph.add_edge(
            int(row.src),
            int(row.dst),
            sum_kzt=float(row.sum_kzt),
            n_tx=int(row.n_tx),
            depth=int(row.depth),
        )
    return graph


def compute_node_features(
    dataset: DatasetTables,
    graph: nx.DiGraph | None = None,
    *,
    betweenness_sample_size: int = BETWEENNESS_SAMPLE_SIZE,
    random_seed: int = RANDOM_SEED,
) -> pd.DataFrame:
    """Compute the authoritative v1 structural and temporal feature table."""

    if betweenness_sample_size <= 0:
        raise ValueError("betweenness_sample_size must be positive")
    directed = graph or build_directed_graph(dataset.nodes, dataset.edges)
    if set(directed.nodes) != set(dataset.nodes["gid"].tolist()):
        raise ValueError("Graph node set does not match validated nodes.")

    node_count = directed.number_of_nodes()
    in_degree = dict(directed.in_degree())
    out_degree = dict(directed.out_degree())
    in_kzt = dict(directed.in_degree(weight="sum_kzt"))
    out_kzt = dict(directed.out_degree(weight="sum_kzt"))
    in_tx = dict(directed.in_degree(weight="n_tx"))
    out_tx = dict(directed.out_degree(weight="n_tx"))

    pagerank = (
        nx.pagerank(directed, weight="sum_kzt", max_iter=200, tol=1.0e-12) if node_count else {}
    )
    sample_count = min(betweenness_sample_size, node_count)
    betweenness = (
        nx.betweenness_centrality(
            directed,
            k=sample_count,
            normalized=True,
            weight=None,
            endpoints=False,
            seed=random_seed,
        )
        if node_count
        else {}
    )
    seed_reach = _seed_reach_counts(
        directed,
        dataset.nodes.loc[dataset.nodes["is_seed"], "gid"].tolist(),
    )
    rapid_outflow = _rapid_outflow_ratios(dataset.transactions)

    features = dataset.nodes.loc[:, ["gid", "depth", "is_seed"]].copy()
    features["in_degree"] = features["gid"].map(in_degree).fillna(0).astype("int64")
    features["out_degree"] = features["gid"].map(out_degree).fillna(0).astype("int64")
    features["in_kzt"] = features["gid"].map(in_kzt).fillna(0.0).astype("float64")
    features["out_kzt"] = features["gid"].map(out_kzt).fillna(0.0).astype("float64")
    features["in_tx"] = features["gid"].map(in_tx).fillna(0).astype("int64")
    features["out_tx"] = features["gid"].map(out_tx).fillna(0).astype("int64")

    observed_inflow = features["in_kzt"] > 0.0
    supported_pass_through = observed_inflow & ~features["is_seed"]
    features["pass_through_ratio"] = np.where(
        supported_pass_through,
        features["out_kzt"] / features["in_kzt"].where(observed_inflow),
        np.nan,
    )
    features["pagerank"] = features["gid"].map(pagerank).fillna(0.0).astype("float64")
    features["betweenness"] = features["gid"].map(betweenness).fillna(0.0).astype("float64")
    features["seed_reach_count"] = features["gid"].map(seed_reach).fillna(0).astype("int64")
    features["rapid_outflow_ratio"] = features["gid"].map(rapid_outflow).astype("float64")
    features["truncated_by_depth"] = (features["depth"] == 4) & (features["out_degree"] == 0)

    percentile_sources = {
        "in_degree_percentile": "in_degree",
        "out_degree_percentile": "out_degree",
        "in_kzt_percentile": "in_kzt",
        "out_kzt_percentile": "out_kzt",
        "pagerank_percentile": "pagerank",
        "betweenness_percentile": "betweenness",
        "seed_reach_percentile": "seed_reach_count",
    }
    for destination, source in percentile_sources.items():
        features[destination] = percentile_rank(features[source])

    features["max_kzt"] = features[["in_kzt", "out_kzt"]].max(axis=1)
    features["max_kzt_percentile"] = percentile_rank(features["max_kzt"])
    features["coordinator_index"] = (
        0.30 * features["betweenness_percentile"]
        + 0.25 * features["seed_reach_percentile"]
        + 0.20 * features["pagerank_percentile"]
        + 0.15 * features["in_degree_percentile"]
        + 0.10 * features["out_degree_percentile"]
    ).clip(0.0, 1.0)
    features["coordinator_index_percentile"] = percentile_rank(features["coordinator_index"])
    features["uncertainty_flags"] = features.apply(_uncertainty_flags, axis=1)

    return features.sort_values("gid", kind="mergesort").reset_index(drop=True)


def percentile_rank(values: pd.Series) -> pd.Series:
    """Average-tie percentile rank on ``[0, 1]`` for a complete series."""

    if values.isna().any():
        raise ValueError("Percentile inputs cannot contain null values.")
    return values.rank(method="average", pct=True).astype("float64")


def _seed_reach_counts(graph: nx.DiGraph, seed_gids: Iterable[int]) -> dict[int, int]:
    counts = {int(gid): 0 for gid in graph.nodes}
    for seed_gid in sorted(int(gid) for gid in seed_gids):
        # NetworkX descendants exclude the source itself.  A seed contributes
        # only when it is genuinely upstream of another observed node.
        for descendant in nx.descendants(graph, seed_gid):
            counts[int(descendant)] += 1
    return counts


def _rapid_outflow_ratios(transactions: pd.DataFrame) -> dict[int, float]:
    """Calculate amount share sent 0--2 calendar days after observed inflow.

    An outgoing transaction is counted once when *any* incoming transaction
    occurred on the same or preceding two calendar days.  With date-only data,
    same-day causality is intentionally not inferred; the signal is supporting
    evidence and receives an explicit uncertainty flag.
    """

    incoming_dates: dict[int, np.ndarray] = {}
    for gid, group in transactions.groupby("dst", sort=True):
        incoming_dates[int(gid)] = np.sort(group["date"].to_numpy(dtype="datetime64[D]", copy=True))

    ratios: dict[int, float] = {}
    for gid, outgoing in transactions.groupby("src", sort=True):
        gid_int = int(gid)
        observed_incoming = incoming_dates.get(gid_int)
        if observed_incoming is None or observed_incoming.size == 0:
            ratios[gid_int] = float("nan")
            continue

        dates = outgoing["date"].to_numpy(dtype="datetime64[D]", copy=False)
        positions = np.searchsorted(observed_incoming, dates, side="right") - 1
        valid_prior = positions >= 0
        qualifying = np.zeros(len(outgoing), dtype=bool)
        if valid_prior.any():
            deltas = (
                (dates[valid_prior] - observed_incoming[positions[valid_prior]])
                .astype("timedelta64[D]")
                .astype("int64")
            )
            qualifying[valid_prior] = deltas <= 2

        amounts = outgoing["sum_kzt"].to_numpy(dtype="float64", copy=False)
        denominator = float(amounts.sum())
        ratios[gid_int] = (
            float(amounts[qualifying].sum() / denominator) if denominator > 0.0 else float("nan")
        )
    return ratios


def _uncertainty_flags(row: pd.Series) -> tuple[str, ...]:
    flags: list[str] = []
    if bool(row["is_seed"]):
        flags.append("seed_inflow_incomplete")
    if bool(row["truncated_by_depth"]):
        flags.append("depth_boundary")
    if int(row["in_degree"]) == 0 and int(row["out_degree"]) == 0:
        flags.append("isolated_node")
    if pd.isna(row["rapid_outflow_ratio"]):
        flags.append("temporal_signal_unavailable")
    else:
        flags.append("date_only_resolution")
    return tuple(flags)
