from __future__ import annotations

import pandas as pd

from aml_agent.analytics.clustering import summarize_clusters
from aml_agent.analytics.roles import assign_roles, score_and_rank


def test_role_precedence_and_seed_distributor_rule() -> None:
    features = pd.DataFrame(
        {
            "gid": pd.Series([1, 2, 3, 4, 5, 6, 7], dtype="int64"),
            "depth": [1, 1, 1, 2, 3, 4, 0],
            "is_seed": [False, False, False, False, False, False, True],
            "in_degree": [1, 5, 1, 2, 2, 2, 0],
            "out_degree": [1, 1, 10, 1, 0, 0, 10],
            "in_kzt": [10_000.0, 100_000.0, 10_000.0, 20_000.0, 30_000.0, 30_000.0, 0.0],
            "out_kzt": [10_000.0, 20_000.0, 100_000.0, 20_000.0, 0.0, 0.0, 100_000.0],
            "pass_through_ratio": [1.0, 0.2, 10.0, 1.0, 0.0, 0.0, float("nan")],
            "rapid_outflow_ratio": [0.5, 0.5, 0.5, 1.0, float("nan"), float("nan"), 0.5],
            "seed_reach_count": [10, 2, 2, 2, 2, 2, 0],
            "seed_reach_percentile": [0.95, 0.5, 0.5, 0.5, 0.5, 0.5, 0.1],
            "pagerank_percentile": [0.95, 0.5, 0.5, 0.5, 0.5, 0.5, 0.1],
            "betweenness_percentile": [0.95, 0.5, 0.5, 0.5, 0.5, 0.5, 0.1],
            "in_degree_percentile": [0.8, 0.9, 0.3, 0.6, 0.6, 0.6, 0.1],
            "out_degree_percentile": [0.5, 0.5, 0.95, 0.5, 0.2, 0.2, 0.95],
            "in_kzt_percentile": [0.5, 0.9, 0.5, 0.6, 0.7, 0.7, 0.1],
            "out_kzt_percentile": [0.5, 0.5, 0.95, 0.6, 0.1, 0.1, 0.95],
            "coordinator_index": [0.95, 0.5, 0.5, 0.5, 0.5, 0.5, 0.1],
            "coordinator_index_percentile": [1.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.1],
            "truncated_by_depth": [False, False, False, False, False, True, False],
        }
    )
    clusters = pd.DataFrame({"gid": pd.Series(range(1, 8), dtype="int64"), "cluster_id": [0] * 7})

    assessments = assign_roles(features, clusters).set_index("gid")

    assert assessments["role"].to_dict() == {
        1: "coordinator",
        2: "consolidator",
        3: "distributor",
        4: "transit",
        5: "terminal",
        6: "peripheral",
        7: "distributor",
    }
    assert assessments["role_score"].between(0.0, 1.0).all()


def test_priority_ties_use_numeric_gid_order() -> None:
    gids = pd.Series([100, 9, 20], dtype="int64")
    features = pd.DataFrame(
        {
            "gid": gids,
            "seed_reach_percentile": [0.5, 0.5, 0.5],
            "pagerank_percentile": [0.5, 0.5, 0.5],
            "betweenness_percentile": [0.5, 0.5, 0.5],
            "max_kzt_percentile": [0.5, 0.5, 0.5],
            "truncated_by_depth": [False, False, False],
        }
    )
    assessments = pd.DataFrame(
        {
            "gid": gids,
            "role": ["peripheral"] * 3,
            "role_score": [0.5] * 3,
            "cluster_id": [0, 0, 0],
            "evidence": ["Observed 1 indicator."] * 3,
            "ruleset_version": ["v1"] * 3,
        }
    )

    _, ranked = score_and_rank(features, assessments, top_n=3)

    assert ranked["gid"].tolist() == [9, 20, 100]
    assert ranked["rank"].tolist() == [1, 2, 3]


def test_cluster_hypothesis_uses_the_highest_priority_role() -> None:
    features = pd.DataFrame(
        {
            "gid": pd.Series([1, 2], dtype="int64"),
            "is_seed": [False, False],
        }
    )
    assessments = pd.DataFrame(
        {
            "gid": pd.Series([1, 2], dtype="int64"),
            "cluster_id": pd.Series([0, 0], dtype="int64"),
            "role": ["peripheral", "distributor"],
            "role_score": [0.9, 0.6],
            "priority_score": [0.9, 0.6],
        }
    )
    edges = pd.DataFrame(
        {
            "src": pd.Series(dtype="int64"),
            "dst": pd.Series(dtype="int64"),
            "sum_kzt": pd.Series(dtype="float64"),
        }
    )

    clusters = summarize_clusters(features, assessments, edges)

    assert clusters.loc[0, "hypothesis"] == (
        "Transfer community without a dominant structural pattern"
    )
