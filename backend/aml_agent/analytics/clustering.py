"""Deterministic Louvain community detection and cluster summaries."""

from __future__ import annotations

from typing import Final

import networkx as nx
import pandas as pd

LOUVAIN_RESOLUTION: Final[float] = 1.0
RANDOM_SEED: Final[int] = 42


def build_undirected_projection(nodes: pd.DataFrame, edges: pd.DataFrame) -> nx.Graph:
    """Project directed edges while summing reciprocal observed amounts."""

    projection = nx.Graph()
    projection.add_nodes_from(int(gid) for gid in sorted(nodes["gid"].tolist()))
    for row in edges.sort_values(["src", "dst"], kind="mergesort").itertuples(index=False):
        src, dst, amount = int(row.src), int(row.dst), float(row.sum_kzt)
        previous = float(projection.get_edge_data(src, dst, default={}).get("sum_kzt", 0.0))
        projection.add_edge(src, dst, sum_kzt=previous + amount)
    return projection


def cluster_network(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    *,
    resolution: float = LOUVAIN_RESOLUTION,
    random_seed: int = RANDOM_SEED,
) -> pd.DataFrame:
    """Assign one stable integer ``cluster_id`` to every node.

    Louvain is run on the complete projection with the documented seed.  An
    explicit postcondition ensures isolates remain singleton communities.  IDs
    are stable by ``(-community size, minimum GID)``.
    """

    if resolution <= 0.0:
        raise ValueError("resolution must be positive")
    projection = build_undirected_projection(nodes, edges)
    isolate_set = {int(gid) for gid in nx.isolates(projection)}

    communities: list[set[int]] = []
    if projection.number_of_nodes():
        detected = nx.community.louvain_communities(
            projection,
            weight="sum_kzt",
            resolution=resolution,
            seed=random_seed,
        )
        communities.extend({int(gid) for gid in community} for community in detected)
    if any(len(community) != 1 for community in communities if community & isolate_set):
        raise RuntimeError("Louvain assigned an isolated node to a non-singleton cluster.")
    communities.sort(key=lambda community: (-len(community), min(community)))

    rows = [
        {"gid": gid, "cluster_id": cluster_id}
        for cluster_id, community in enumerate(communities)
        for gid in sorted(community)
    ]
    assignments = pd.DataFrame(rows, columns=["gid", "cluster_id"])
    if len(assignments) != len(nodes) or assignments["gid"].nunique() != len(nodes):
        raise RuntimeError("Community detection did not assign every node exactly once.")
    return (
        assignments.astype({"gid": "int64", "cluster_id": "int64"})
        .sort_values("gid", kind="mergesort")
        .reset_index(drop=True)
    )


def summarize_clusters(
    features: pd.DataFrame,
    assessments: pd.DataFrame,
    edges: pd.DataFrame,
) -> pd.DataFrame:
    """Build the required deterministic ``clusters.csv`` table."""

    merged = features.merge(
        assessments.loc[:, ["gid", "cluster_id", "role", "role_score", "priority_score"]],
        on="gid",
        how="inner",
        validate="one_to_one",
    )
    if len(merged) != len(features):
        raise ValueError("Cluster summary requires one assessment per feature row.")

    cluster_by_gid = dict(zip(merged["gid"], merged["cluster_id"], strict=True))
    internal_amounts = {int(cluster_id): 0.0 for cluster_id in merged["cluster_id"].unique()}
    for row in edges.itertuples(index=False):
        src_cluster = int(cluster_by_gid[int(row.src)])
        if src_cluster == int(cluster_by_gid[int(row.dst)]):
            internal_amounts[src_cluster] += float(row.sum_kzt)

    base_rows: list[dict[str, object]] = []
    for cluster_id, group in merged.groupby("cluster_id", sort=True):
        ranked = group.sort_values(
            ["priority_score", "role_score", "gid"],
            ascending=[False, False, True],
            kind="mergesort",
        )
        base_rows.append(
            {
                "cluster_id": int(cluster_id),
                "n_nodes": int(len(group)),
                "n_seed": int(group["is_seed"].sum()),
                "sum_kzt_internal": float(internal_amounts[int(cluster_id)]),
                "top_gids": [str(int(gid)) for gid in ranked["gid"].head(5)],
                "roles": tuple(group["role"].tolist()),
                "dominant_role": str(ranked.iloc[0]["role"]),
            }
        )

    summaries = pd.DataFrame(base_rows)
    turnover_threshold = float(summaries["sum_kzt_internal"].quantile(0.75))
    summaries["hypothesis"] = summaries.apply(
        lambda row: _cluster_hypothesis(row, turnover_threshold), axis=1
    )
    return (
        summaries.loc[
            :,
            [
                "cluster_id",
                "n_nodes",
                "n_seed",
                "sum_kzt_internal",
                "top_gids",
                "hypothesis",
            ],
        ]
        .sort_values("cluster_id", kind="mergesort")
        .reset_index(drop=True)
    )


def _cluster_hypothesis(row: pd.Series, turnover_threshold: float) -> str:
    roles = tuple(row["roles"])
    dominant_role = str(row["dominant_role"])
    if int(row["n_seed"]) >= 2 and float(row["sum_kzt_internal"]) >= turnover_threshold:
        return "Multi-seed connected transfer community for analyst review"
    if dominant_role == "distributor":
        return "Community organized around a distribution pattern"
    if dominant_role == "consolidator":
        return "Community with observed consolidation indicators"
    if roles and roles.count("terminal") / len(roles) >= 0.5:
        return "Recipient-heavy community with limited visible onward flow"
    return "Transfer community without a dominant structural pattern"
