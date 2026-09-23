from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aml_agent.analytics import (
    ROLES,
    DatasetValidationError,
    analyze_dataset,
    analyze_frames,
    validate_dataset,
)


@pytest.fixture
def sample_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    nodes = pd.DataFrame(
        {
            "gid": pd.Series([1, 2, 3, 4, 5, 6], dtype="int64"),
            "depth": pd.Series([0, 1, 1, 4, 1, 0], dtype="int64"),
            "is_seed": pd.Series([True, False, False, False, False, True], dtype="bool"),
        }
    )
    edges = pd.DataFrame(
        {
            "src": pd.Series([1, 1, 2, 3, 5], dtype="int64"),
            "dst": pd.Series([2, 3, 4, 4, 1], dtype="int64"),
            "sum_kzt": pd.Series([6_000.0, 7_000.0, 6_000.0, 7_000.0, 5_000.0], dtype="float64"),
            "n_tx": pd.Series([1, 1, 1, 1, 1], dtype="int64"),
            "depth": pd.Series([1, 1, 2, 2, 1], dtype="int64"),
        }
    )
    transactions = pd.DataFrame(
        {
            "src": pd.Series([1, 1, 2, 3, 5], dtype="int64"),
            "dst": pd.Series([2, 3, 4, 4, 1], dtype="int64"),
            "date": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-01-02", "2026-01-03", "2026-01-01"]
            ),
            "sum_kzt": pd.Series([6_000.0, 7_000.0, 6_000.0, 7_000.0, 5_000.0], dtype="float64"),
        }
    )
    return nodes, edges, transactions


def test_boundary_seed_and_all_node_invariants(
    sample_frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> None:
    result = analyze_frames(*sample_frames, top_n=20)

    boundary = result.assessments.loc[result.assessments["gid"] == 4].iloc[0]
    seed_feature = result.features.loc[result.features["gid"] == 1].iloc[0]
    orphan = result.assessments.loc[result.assessments["gid"] == 6].iloc[0]

    assert boundary["role"] != "terminal"
    assert bool(result.features.loc[result.features["gid"] == 4, "truncated_by_depth"].iloc[0])
    assert np.isnan(seed_feature["pass_through_ratio"])
    assert orphan["role"] == "peripheral"
    assert len(result.assessments) == len(sample_frames[0])
    assert result.assessments["gid"].nunique() == len(sample_frames[0])
    assert result.assessments["cluster_id"].notna().all()


def test_scores_evidence_and_temporal_signal_are_bounded(
    sample_frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> None:
    result = analyze_frames(*sample_frames)

    assert result.assessments["role"].isin(ROLES).all()
    assert result.assessments["role_score"].between(0.0, 1.0).all()
    assert result.assessments["priority_score"].between(0.0, 1.0).all()
    assert result.assessments["evidence"].str.len().between(1, 200).all()
    assert result.assessments["evidence"].str.contains(r"\d", regex=True).all()
    assert result.features.loc[result.features["gid"] == 1, "rapid_outflow_ratio"].iloc[0] == 1.0


def test_fingerprint_and_csvs_are_deterministic(
    sample_frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame], tmp_path: Path
) -> None:
    nodes, edges, transactions = sample_frames
    shuffled = validate_dataset(
        nodes.sample(frac=1, random_state=7),
        edges.sample(frac=1, random_state=8),
        transactions.sample(frac=1, random_state=9),
    )
    canonical = validate_dataset(nodes, edges, transactions)
    assert shuffled.sha256 == canonical.sha256

    first = analyze_frames(nodes, edges, transactions, output_dir=tmp_path / "first")
    second = analyze_frames(nodes, edges, transactions, output_dir=tmp_path / "second")
    pd.testing.assert_frame_equal(first.features, second.features)
    pd.testing.assert_frame_equal(first.assessments, second.assessments)
    pd.testing.assert_frame_equal(first.clusters, second.clusters)
    pd.testing.assert_frame_equal(first.top_nodes, second.top_nodes)
    for filename in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
        assert (tmp_path / "first" / filename).read_bytes() == (
            tmp_path / "second" / filename
        ).read_bytes()


def test_transaction_edge_mismatch_is_rejected(
    sample_frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> None:
    nodes, edges, transactions = sample_frames
    invalid = transactions.copy()
    invalid.loc[0, "sum_kzt"] += 1.0

    with pytest.raises(DatasetValidationError, match="do not reconcile"):
        validate_dataset(nodes, edges, invalid)


def test_declared_batch_period_is_enforced(
    sample_frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> None:
    nodes, edges, transactions = sample_frames
    outside = transactions.copy()
    outside.loc[0, "date"] = pd.Timestamp("2025-12-31")

    with pytest.raises(DatasetValidationError, match="declared batch period"):
        validate_dataset(
            nodes,
            edges,
            outside,
            expected_period=("2026-01-01", "2026-01-31"),
        )


def test_public_pipeline_rejects_a_contract_invalid_ranking_size(
    sample_frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> None:
    with pytest.raises(ValueError, match="between 20 and 100"):
        analyze_frames(*sample_frames, top_n=1)


def test_bundled_dataset_full_coverage_and_rerun(tmp_path: Path) -> None:
    data_dir = Path(__file__).resolve().parents[2] / "data"
    if not data_dir.is_dir():
        pytest.skip("Bundled hackathon data is not available.")

    first = analyze_dataset(
        data_dir,
        output_dir=tmp_path / "first",
        top_n=20,
        expected_seed_count=81,
    )
    second = analyze_dataset(
        data_dir,
        output_dir=tmp_path / "second",
        top_n=20,
        expected_seed_count=81,
    )

    assert len(first.features) == 2_248
    assert len(first.assessments) == 2_248
    assert first.assessments["gid"].nunique() == 2_248
    assert len(first.top_nodes) == 20
    assert first.features["truncated_by_depth"].sum() == 444
    boundary_gids = set(first.features.loc[first.features["truncated_by_depth"], "gid"])
    terminal_gids = set(first.assessments.loc[first.assessments["role"] == "terminal", "gid"])
    assert boundary_gids.isdisjoint(terminal_gids)
    assert first.dataset_sha256 == second.dataset_sha256
    for filename in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
        assert (tmp_path / "first" / filename).read_bytes() == (
            tmp_path / "second" / filename
        ).read_bytes()
