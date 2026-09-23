"""End-to-end contract tests for the controlled Phase 2 tool runtime."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pandas as pd
import pytest

from aml_agent.storage import ArtifactStore, Database, RunState, VerificationStatus
from aml_agent.tool_runtime import ToolRuntime


@dataclass(slots=True)
class RuntimeHarness:
    runtime: ToolRuntime
    database: Database
    artifacts: ArtifactStore
    data_dir: Path
    run_id: str


@pytest.fixture
def runtime_harness(tmp_path: Path) -> RuntimeHarness:
    data_dir = tmp_path / "dataset"
    _write_synthetic_dataset(data_dir, node_count=25)
    database = Database(tmp_path / "aml-agent.sqlite3")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    runtime = ToolRuntime(
        database,
        artifacts,
        {"synthetic-v1": data_dir},
        expected_seed_counts={"synthetic-v1": 1},
    )
    run = runtime.create_run("synthetic-v1")
    try:
        yield RuntimeHarness(runtime, database, artifacts, data_dir, run.run_id)
    finally:
        database.close()


def _write_synthetic_dataset(data_dir: Path, *, node_count: int) -> None:
    data_dir.mkdir(parents=True)
    gids = list(range(1, node_count + 1))
    nodes = pd.DataFrame(
        {
            "gid": pd.Series(gids, dtype="int64"),
            "depth": pd.Series([0] + [1] * (node_count - 1), dtype="int64"),
            "is_seed": pd.Series([True] + [False] * (node_count - 1), dtype="bool"),
        }
    )
    src = gids[:-1]
    dst = gids[1:]
    amounts = [float(5_000 + index) for index in range(len(src))]
    edges = pd.DataFrame(
        {
            "src": pd.Series(src, dtype="int64"),
            "dst": pd.Series(dst, dtype="int64"),
            "sum_kzt": pd.Series(amounts, dtype="float64"),
            "n_tx": pd.Series([1] * len(src), dtype="int64"),
            "depth": pd.Series([1] * len(src), dtype="int64"),
        }
    )
    transactions = pd.DataFrame(
        {
            "src": pd.Series(src, dtype="int64"),
            "dst": pd.Series(dst, dtype="int64"),
            "date": pd.to_datetime(["2026-01-01"] * len(src)),
            "sum_kzt": pd.Series(amounts, dtype="float64"),
        }
    )
    nodes.to_parquet(data_dir / "nodes.parquet", index=False)
    edges.to_parquet(data_dir / "edges.parquet", index=False)
    transactions.to_parquet(data_dir / "transactions.parquet", index=False)


def _advance_to_ranked(harness: RuntimeHarness, *, limit: int = 20) -> dict[str, object]:
    calls = (
        ("inspect_dataset", {"run_id": harness.run_id}),
        ("build_graph", {"run_id": harness.run_id}),
        (
            "compute_graph_features",
            {"run_id": harness.run_id, "include_temporal": True},
        ),
        (
            "cluster_network",
            {"run_id": harness.run_id, "resolution": 1.0, "random_seed": 42},
        ),
        (
            "assign_roles",
            {"run_id": harness.run_id, "ruleset_version": "v1"},
        ),
        ("rank_targets", {"run_id": harness.run_id, "limit": limit}),
    )
    result: dict[str, object] = {}
    for name, arguments in calls:
        result = harness.runtime.execute(name, arguments)
        assert result["ok"] is True, result
    return result


def _create_case(harness: RuntimeHarness, ranking: dict[str, object]) -> dict[str, object]:
    return harness.runtime.execute(
        "create_review_case",
        {
            "run_id": harness.run_id,
            "target_gids": ranking["data"]["target_gids"],
            "title": "Priority structural indicators for analyst review",
        },
    )


def _fail_result_persistence_once(
    monkeypatch: pytest.MonkeyPatch,
    harness: RuntimeHarness,
    target_tool: str,
) -> None:
    original = harness.runtime._persist_result
    armed = True

    def injected_failure(
        tool_name: str,
        arguments: dict[str, object],
        idempotency_key: str,
        envelope: dict[str, object],
    ) -> None:
        nonlocal armed
        if armed and tool_name == target_tool:
            armed = False
            raise RuntimeError("injected result persistence failure")
        original(tool_name, arguments, idempotency_key, envelope)

    monkeypatch.setattr(harness.runtime, "_persist_result", injected_failure)


def test_invalid_state_is_rejected_without_transition(runtime_harness: RuntimeHarness) -> None:
    result = runtime_harness.runtime.execute(
        "build_graph",
        {"run_id": runtime_harness.run_id},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_STATE"
    assert runtime_harness.database.require_run(runtime_harness.run_id).status is RunState.CREATED
    events = runtime_harness.database.list_events(runtime_harness.run_id)
    assert events[-1].payload == {"error_code": "INVALID_STATE", "ok": False}


def test_failed_state_rejects_tools_with_invalid_state(
    runtime_harness: RuntimeHarness,
) -> None:
    runtime_harness.database.transition_run(runtime_harness.run_id, RunState.FAILED)

    result = runtime_harness.runtime.execute(
        "inspect_dataset",
        {"run_id": runtime_harness.run_id},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_STATE"


def test_verification_failed_state_rejects_tools_with_invalid_state(
    runtime_harness: RuntimeHarness,
) -> None:
    _advance_to_ranked(runtime_harness)
    runtime_harness.database.record_verification(
        runtime_harness.run_id,
        passed=False,
        checks={"injected": False},
    )

    result = runtime_harness.runtime.execute(
        "verify_run",
        {"run_id": runtime_harness.run_id},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_STATE"


def test_schema_errors_return_safe_envelope(runtime_harness: RuntimeHarness) -> None:
    result = runtime_harness.runtime.execute(
        "inspect_dataset",
        {"run_id": runtime_harness.run_id, "path": "../../secret"},
    )

    assert result["ok"] is False
    assert result["error"] == {
        "code": "INVALID_ARGUMENT",
        "message": "Tool arguments do not match the strict schema.",
        "retriable": False,
        "details": {"field": "$"},
    }
    assert runtime_harness.database.list_events(runtime_harness.run_id) == []


def test_expected_period_registry_is_scoped_to_known_datasets(tmp_path: Path) -> None:
    data_dir = tmp_path / "dataset"
    _write_synthetic_dataset(data_dir, node_count=25)
    with Database(tmp_path / "period.sqlite3") as database:
        artifacts = ArtifactStore(tmp_path / "period-artifacts")
        with pytest.raises(ValueError, match="unregistered dataset"):
            ToolRuntime(
                database,
                artifacts,
                {"synthetic-v1": data_dir},
                expected_periods={"unknown": ("2026-01-01", "2026-01-31")},
            )
        runtime = ToolRuntime(
            database,
            artifacts,
            {"synthetic-v1": data_dir},
            expected_periods={"synthetic-v1": ("2026-01-02", "2026-01-31")},
        )
        run = runtime.create_run("synthetic-v1")

        result = runtime.execute("inspect_dataset", {"run_id": run.run_id})

        assert result["ok"] is False
        assert result["error"]["code"] == "DATASET_SCHEMA_INVALID"
        assert database.require_run(run.run_id).status is RunState.CREATED


def test_identical_call_replays_persisted_result(runtime_harness: RuntimeHarness) -> None:
    arguments = {"run_id": runtime_harness.run_id}

    first = runtime_harness.runtime.execute("inspect_dataset", arguments)
    first_event_count = len(runtime_harness.database.list_events(runtime_harness.run_id))
    second = runtime_harness.runtime.execute("inspect_dataset", arguments)

    assert first == second
    assert first["ok"] is True
    assert runtime_harness.database.require_run(runtime_harness.run_id).status is RunState.VALIDATED
    assert first_event_count == 6
    assert len(runtime_harness.database.list_events(runtime_harness.run_id)) == first_event_count


def test_changed_arguments_conflict_after_success(runtime_harness: RuntimeHarness) -> None:
    _advance_to_ranked(runtime_harness, limit=20)

    conflict = runtime_harness.runtime.execute(
        "rank_targets",
        {"run_id": runtime_harness.run_id, "limit": 21},
    )

    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "INVALID_ARGUMENT"
    assert runtime_harness.database.require_run(runtime_harness.run_id).status is RunState.RANKED


def test_case_requires_exact_ranked_order_and_bad_call_does_not_poison_retry(
    runtime_harness: RuntimeHarness,
) -> None:
    ranking = _advance_to_ranked(runtime_harness)
    targets = list(ranking["data"]["target_gids"])
    targets[0], targets[1] = targets[1], targets[0]

    rejected = runtime_harness.runtime.execute(
        "create_review_case",
        {
            "run_id": runtime_harness.run_id,
            "target_gids": targets,
            "title": "Priority structural indicators for analyst review",
        },
    )
    accepted = _create_case(runtime_harness, ranking)

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "INVALID_ARGUMENT"
    assert accepted["ok"] is True
    assert accepted["data"]["target_count"] == 20
    assert runtime_harness.database.require_run(runtime_harness.run_id).status is (
        RunState.CASE_CREATED
    )


def test_case_requires_the_complete_persisted_ranking(
    runtime_harness: RuntimeHarness,
) -> None:
    ranking = _advance_to_ranked(runtime_harness, limit=21)
    targets = list(ranking["data"]["target_gids"])

    rejected = runtime_harness.runtime.execute(
        "create_review_case",
        {
            "run_id": runtime_harness.run_id,
            "target_gids": targets[:20],
            "title": "Priority structural indicators for analyst review",
        },
    )
    accepted = _create_case(runtime_harness, ranking)

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "INVALID_ARGUMENT"
    assert accepted["ok"] is True
    assert accepted["data"]["target_count"] == 21


def test_case_created_recovers_when_result_persistence_failed(
    runtime_harness: RuntimeHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ranking = _advance_to_ranked(runtime_harness)
    arguments = {
        "run_id": runtime_harness.run_id,
        "target_gids": ranking["data"]["target_gids"],
        "title": "Priority structural indicators for analyst review",
    }
    _fail_result_persistence_once(monkeypatch, runtime_harness, "create_review_case")

    interrupted = runtime_harness.runtime.execute("create_review_case", arguments)
    assert interrupted["ok"] is False
    assert interrupted["error"]["code"] == "INTERNAL_ERROR"
    assert runtime_harness.database.require_run(runtime_harness.run_id).status is (
        RunState.CASE_CREATED
    )

    changed = runtime_harness.runtime.execute(
        "create_review_case",
        {**arguments, "title": "Network indicators for analyst review"},
    )
    recovered = runtime_harness.runtime.execute("create_review_case", arguments)

    assert changed["ok"] is False
    assert changed["error"]["code"] == "INVALID_ARGUMENT"
    assert recovered["ok"] is True
    assert recovered["data"]["idempotent_replay"] is True


@pytest.mark.parametrize(
    "title",
    [
        "Network case 2026",
        "Criminal indicators for review",
        "Money launderer candidates for review",
        "Преступники: кандидаты для проверки",
    ],
)
def test_case_title_safety_is_enforced_at_tool_boundary(
    runtime_harness: RuntimeHarness,
    title: str,
) -> None:
    ranking = _advance_to_ranked(runtime_harness)

    rejected = runtime_harness.runtime.execute(
        "create_review_case",
        {
            "run_id": runtime_harness.run_id,
            "target_gids": ranking["data"]["target_gids"],
            "title": title,
        },
    )

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "INVALID_ARGUMENT"
    assert runtime_harness.database.require_run(runtime_harness.run_id).status is RunState.RANKED


def test_cache_rebuild_returns_json_safe_bounded_evidence(
    runtime_harness: RuntimeHarness,
) -> None:
    ranking = _advance_to_ranked(runtime_harness)
    gid = ranking["data"]["target_gids"][0]
    restarted = ToolRuntime(
        runtime_harness.database,
        runtime_harness.artifacts,
        {"synthetic-v1": runtime_harness.data_dir},
        expected_seed_counts={"synthetic-v1": 1},
    )

    evidence = restarted.execute(
        "get_node_evidence",
        {"run_id": runtime_harness.run_id, "gid": gid, "neighbor_hops": 2},
    )

    assert evidence["ok"] is True
    assert evidence["data"]["node"]["gid"] == gid
    assert isinstance(evidence["data"]["node"]["gid"], str)
    assert all(isinstance(edge["src"], str) for edge in evidence["data"]["incoming_edges"])
    assert all(isinstance(edge["dst"], str) for edge in evidence["data"]["outgoing_edges"])


def test_export_tamper_is_caught_and_run_never_completes(
    runtime_harness: RuntimeHarness,
) -> None:
    ranking = _advance_to_ranked(runtime_harness)
    assert _create_case(runtime_harness, ranking)["ok"] is True
    exported = runtime_harness.runtime.execute(
        "export_results",
        {"run_id": runtime_harness.run_id, "include_audit": True},
    )
    assert exported["ok"] is True
    top_path = runtime_harness.artifacts.path_for(runtime_harness.run_id, "top_nodes.csv")
    top_path.write_bytes(top_path.read_bytes() + b"\n")

    verified = runtime_harness.runtime.execute(
        "verify_run",
        {"run_id": runtime_harness.run_id},
    )

    assert verified["ok"] is False
    assert verified["error"]["code"] == "VERIFICATION_FAILED"
    assert "top_nodes.csv:integrity" in verified["data"]["failed_checks"]
    run = runtime_harness.database.require_run(runtime_harness.run_id)
    assert run.status is RunState.VERIFICATION_FAILED
    assert run.verification_status is VerificationStatus.FAILED


def test_exported_state_recovers_when_result_persistence_failed(
    runtime_harness: RuntimeHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ranking = _advance_to_ranked(runtime_harness)
    assert _create_case(runtime_harness, ranking)["ok"] is True
    arguments = {"run_id": runtime_harness.run_id, "include_audit": False}
    _fail_result_persistence_once(monkeypatch, runtime_harness, "export_results")

    interrupted = runtime_harness.runtime.execute("export_results", arguments)
    assert interrupted["ok"] is False
    assert interrupted["error"]["code"] == "INTERNAL_ERROR"
    assert runtime_harness.database.require_run(runtime_harness.run_id).status is (
        RunState.EXPORTED
    )

    changed = runtime_harness.runtime.execute(
        "export_results",
        {**arguments, "include_audit": True},
    )
    recovered = runtime_harness.runtime.execute("export_results", arguments)

    assert changed["ok"] is False
    assert changed["error"]["code"] == "INVALID_ARGUMENT"
    assert recovered["ok"] is True
    assert set(recovered["data"]["artifact_names"]) == {
        "nodes_roles.csv",
        "clusters.csv",
        "top_nodes.csv",
    }


def test_verified_state_recovers_and_completes_when_result_persistence_failed(
    runtime_harness: RuntimeHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ranking = _advance_to_ranked(runtime_harness)
    assert _create_case(runtime_harness, ranking)["ok"] is True
    assert (
        runtime_harness.runtime.execute(
            "export_results",
            {"run_id": runtime_harness.run_id, "include_audit": False},
        )["ok"]
        is True
    )
    arguments = {"run_id": runtime_harness.run_id}
    _fail_result_persistence_once(monkeypatch, runtime_harness, "verify_run")

    interrupted = runtime_harness.runtime.execute("verify_run", arguments)
    assert interrupted["ok"] is False
    assert interrupted["error"]["code"] == "INTERNAL_ERROR"
    assert runtime_harness.database.require_run(runtime_harness.run_id).status is (
        RunState.VERIFIED
    )

    recovered = runtime_harness.runtime.execute("verify_run", arguments)

    assert recovered["ok"] is True
    assert recovered["data"]["passed"] is True
    assert runtime_harness.database.require_run(runtime_harness.run_id).status is (
        RunState.COMPLETED
    )


def test_authoritative_recompute_catches_non_top_role_and_evidence_tamper(
    runtime_harness: RuntimeHarness,
) -> None:
    ranking = _advance_to_ranked(runtime_harness)
    assert _create_case(runtime_harness, ranking)["ok"] is True
    assert (
        runtime_harness.runtime.execute(
            "export_results",
            {"run_id": runtime_harness.run_id, "include_audit": False},
        )["ok"]
        is True
    )

    nodes_path = runtime_harness.artifacts.path_for(
        runtime_harness.run_id,
        "nodes_roles.csv",
    )
    nodes = pd.read_csv(nodes_path, dtype={"gid": "string"})
    top_gids = set(ranking["data"]["target_gids"])
    selected = next(index for index, gid in nodes["gid"].items() if gid not in top_gids)
    nodes.at[selected, "role"] = (
        "coordinator" if nodes.at[selected, "role"] != "coordinator" else "peripheral"
    )
    nodes.at[selected, "evidence"] = "Observed 99 deterministic indicators for review."
    nodes.to_csv(nodes_path, index=False, lineterminator="\n")
    raw = nodes_path.read_bytes()
    runtime_harness.database.register_artifact(
        runtime_harness.run_id,
        name="nodes_roles.csv",
        relative_path=f"{runtime_harness.run_id}/nodes_roles.csv",
        sha256=sha256(raw).hexdigest(),
        row_count=len(nodes),
        size_bytes=len(raw),
    )

    verified = runtime_harness.runtime.execute(
        "verify_run",
        {"run_id": runtime_harness.run_id},
    )

    assert verified["ok"] is False
    assert "nodes_roles_authoritative" in verified["data"]["failed_checks"]


def test_synthetic_golden_path_exports_verifies_and_completes(
    runtime_harness: RuntimeHarness,
) -> None:
    ranking = _advance_to_ranked(runtime_harness)
    case = _create_case(runtime_harness, ranking)
    exported = runtime_harness.runtime.execute(
        "export_results",
        {"run_id": runtime_harness.run_id, "include_audit": True},
    )
    verified = runtime_harness.runtime.execute(
        "verify_run",
        {"run_id": runtime_harness.run_id},
    )

    assert case["ok"] is True
    assert set(exported["data"]["artifact_names"]) == {
        "nodes_roles.csv",
        "clusters.csv",
        "top_nodes.csv",
        "audit.json",
    }
    assert verified["ok"] is True
    assert verified["data"]["passed"] is True
    run = runtime_harness.database.require_run(runtime_harness.run_id)
    assert run.status is RunState.COMPLETED
    assert run.verification_status is VerificationStatus.PASSED
    event_count = len(runtime_harness.database.list_events(runtime_harness.run_id))

    stale_verify = runtime_harness.runtime.execute(
        "verify_run",
        {"run_id": runtime_harness.run_id},
    )

    assert stale_verify["ok"] is False
    assert stale_verify["error"]["code"] == "INVALID_STATE"
    inspect = runtime_harness.runtime.execute(
        "inspect_dataset",
        {"run_id": runtime_harness.run_id},
    )
    assert inspect["ok"] is False
    assert inspect["error"]["code"] == "INVALID_STATE"

    nodes_path = runtime_harness.artifacts.path_for(
        runtime_harness.run_id,
        "nodes_roles.csv",
    )
    nodes_path.write_bytes(nodes_path.read_bytes() + b"\n")
    stale_after_tamper = runtime_harness.runtime.execute(
        "verify_run",
        {"run_id": runtime_harness.run_id},
    )
    assert stale_after_tamper["ok"] is False
    assert stale_after_tamper["error"]["code"] == "INVALID_STATE"
    assert len(runtime_harness.database.list_events(runtime_harness.run_id)) == event_count
    with runtime_harness.database.transaction(immediate=False) as connection:
        tool_result_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM tool_results WHERE run_id = ?",
                (runtime_harness.run_id,),
            ).fetchone()[0]
        )

    evidence = runtime_harness.runtime.execute(
        "get_node_evidence",
        {
            "run_id": runtime_harness.run_id,
            "gid": ranking["data"]["target_gids"][0],
            "neighbor_hops": 1,
        },
    )

    assert evidence["ok"] is True
    assert len(runtime_harness.database.list_events(runtime_harness.run_id)) == event_count
    with runtime_harness.database.transaction(immediate=False) as connection:
        assert (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM tool_results WHERE run_id = ?",
                    (runtime_harness.run_id,),
                ).fetchone()[0]
            )
            == tool_result_count
        )


def test_bundled_dataset_tool_golden_path(tmp_path: Path) -> None:
    data_dir = Path(__file__).resolve().parents[2] / "data"
    if not data_dir.is_dir():
        pytest.skip("Bundled hackathon data is not available.")
    with Database(tmp_path / "real.sqlite3") as database:
        artifacts = ArtifactStore(tmp_path / "real-artifacts")
        runtime = ToolRuntime(
            database,
            artifacts,
            {"bundled-v1": data_dir},
            expected_seed_counts={"bundled-v1": 81},
            expected_periods={"bundled-v1": ("2026-07-01", "2026-07-31")},
        )
        run = runtime.create_run("bundled-v1")
        harness = RuntimeHarness(runtime, database, artifacts, data_dir, run.run_id)

        ranking = _advance_to_ranked(harness)
        assert ranking["data"]["ranked_count"] == 20
        assert _create_case(harness, ranking)["ok"] is True
        assert (
            runtime.execute(
                "export_results",
                {"run_id": run.run_id, "include_audit": False},
            )["ok"]
            is True
        )
        verified = runtime.execute("verify_run", {"run_id": run.run_id})

        assert verified["ok"] is True
        assert database.require_run(run.run_id).status is RunState.COMPLETED
