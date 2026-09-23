from __future__ import annotations

from hashlib import sha256

import pytest

from aml_agent.storage import (
    Database,
    EventKind,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    RunState,
    ValidationError,
    VerificationStatus,
)


@pytest.fixture
def database(tmp_path):
    with Database(tmp_path / "aml-agent.sqlite3") as instance:
        yield instance


def _new_run(database: Database):
    return database.create_run("bundled-v1", "demo", metadata={"source": "test"})


def _advance_to_ranked(database: Database, run_id: str) -> None:
    for state in (
        RunState.VALIDATED,
        RunState.GRAPH_READY,
        RunState.ANALYZED,
        RunState.CLUSTERED,
        RunState.CLASSIFIED,
        RunState.RANKED,
    ):
        database.transition_run(run_id, state)


def test_run_state_machine_verification_and_completion(database: Database) -> None:
    run = _new_run(database)
    assert run.status is RunState.CREATED
    assert run.verification_status is VerificationStatus.PENDING

    _advance_to_ranked(database, run.run_id)
    created = database.create_review_case(
        run.run_id,
        "Priority structural indicators for analyst review",
        ["100000000000000001", "100000000000000002"],
    )
    assert created.idempotent_replay is False
    assert database.require_run(run.run_id).status is RunState.CASE_CREATED

    for name, row_count in (
        ("nodes_roles.csv", 2_248),
        ("clusters.csv", 91),
        ("top_nodes.csv", 20),
        ("aml_review_report.xlsx", None),
    ):
        database.register_artifact(
            run.run_id,
            name=name,
            relative_path=f"{run.run_id}/{name}",
            sha256=sha256(name.encode()).hexdigest(),
            row_count=row_count,
            size_bytes=len(name),
        )

    exported = database.mark_exported(run.run_id)
    assert exported.status is RunState.EXPORTED
    verified = database.record_verification(
        run.run_id,
        passed=True,
        checks={"all_nodes": True, "artifact_hashes": True},
    )
    assert verified.status is RunState.VERIFIED
    assert verified.verification_status is VerificationStatus.PASSED
    assert verified.verification_payload["all_nodes"] is True

    completed = database.complete_run(run.run_id)
    assert completed.status is RunState.COMPLETED
    with pytest.raises(InvalidStateTransitionError, match="immutable"):
        database.update_run(run.run_id, warning_delta=1)
    with pytest.raises(InvalidStateTransitionError, match="immutable"):
        database.append_event(run.run_id, EventKind.WARNING, "late mutation")
    with pytest.raises(InvalidStateTransitionError, match="immutable"):
        database.put_tool_result(run.run_id, "late_tool", {"ok": True})
    with pytest.raises(InvalidStateTransitionError, match="immutable"):
        database.put_snapshot(run.run_id, "late:snapshot", {"value": 1})
    with pytest.raises(InvalidStateTransitionError, match="immutable"):
        database.register_artifact(
            run.run_id,
            name="top_nodes.csv",
            relative_path=f"{run.run_id}/top_nodes.csv",
            sha256=sha256(b"changed").hexdigest(),
            row_count=20,
            size_bytes=7,
        )
    with pytest.raises(InvalidStateTransitionError, match="immutable"):
        database.record_verification(run.run_id, passed=True, checks={"late": True})


def test_invalid_run_transition_is_rejected_without_mutation(database: Database) -> None:
    run = _new_run(database)

    with pytest.raises(InvalidStateTransitionError, match="created to ranked"):
        database.transition_run(run.run_id, RunState.RANKED)

    persisted = database.require_run(run.run_id)
    assert persisted.status is RunState.CREATED
    assert persisted.revision == 0


def test_review_case_is_idempotent_and_immutable_per_run(database: Database) -> None:
    run = _new_run(database)
    _advance_to_ranked(database, run.run_id)
    title = "Priority structural indicators for analyst review"
    targets = ["100000000000000001", "100000000000000002"]

    first = database.create_review_case(run.run_id, title, targets)
    replay = database.create_review_case(run.run_id, title, targets)

    assert replay.idempotent_replay is True
    assert replay.case.case_id == first.case.case_id
    assert replay.case.target_gids == tuple(targets)

    with pytest.raises(IdempotencyConflictError, match="different review case"):
        database.create_review_case(run.run_id, title, list(reversed(targets)))


@pytest.mark.parametrize(
    "title",
    [
        "Network case 2026",
        "Guilty candidates for review",
        "Fraudster indicators for review",
        "Отмыватели: кандидаты для анализа",
    ],
)
def test_review_case_title_safety_is_enforced_in_storage(
    database: Database,
    title: str,
) -> None:
    run = _new_run(database)
    _advance_to_ranked(database, run.run_id)

    with pytest.raises(ValidationError, match="fixed reviewed MVP titles"):
        database.create_review_case(run.run_id, title, ["100000000000000001"])


def test_cautious_review_case_title_is_allowed_in_storage(database: Database) -> None:
    run = _new_run(database)
    _advance_to_ranked(database, run.run_id)

    result = database.create_review_case(
        run.run_id,
        "Структурные индикаторы для проверки аналитиком",
        ["100000000000000001"],
    )

    assert result.case.title.startswith("Структурные индикаторы")


def test_events_are_monotonic_ordered_and_increment_warning_count(database: Database) -> None:
    run = _new_run(database)
    database.append_event(run.run_id, EventKind.STARTED, "Agent started")
    database.append_event(
        run.run_id,
        EventKind.TOOL_STARTED,
        "Dataset inspection started",
        tool_name="inspect_dataset",
        payload={"attempt": 1},
    )
    database.append_event(
        run.run_id,
        EventKind.WARNING,
        "Depth-boundary recipients are uncertainty-limited",
        payload={"count": 444},
    )

    events = database.list_events(run.run_id)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.kind for event in events] == [
        EventKind.STARTED,
        EventKind.TOOL_STARTED,
        EventKind.WARNING,
    ]
    assert database.list_events(run.run_id, after_sequence=1)[0].sequence == 2
    assert database.require_run(run.run_id).warning_count == 1


def test_tool_results_and_snapshots_are_safe_and_idempotent(database: Database) -> None:
    run = _new_run(database)
    stored = database.put_tool_result(
        run.run_id,
        "inspect_dataset",
        {"ok": True, "counts": {"nodes": 2248}},
        idempotency_key="dataset-v1",
    )
    replay = database.put_tool_result(
        run.run_id,
        "inspect_dataset",
        {"counts": {"nodes": 2248}, "ok": True},
        idempotency_key="dataset-v1",
    )
    assert replay == stored
    assert database.get_tool_result(
        run.run_id, "inspect_dataset", idempotency_key="dataset-v1"
    ) == stored

    with pytest.raises(IdempotencyConflictError, match="different output"):
        database.put_tool_result(
            run.run_id,
            "inspect_dataset",
            {"ok": False},
            idempotency_key="dataset-v1",
        )

    snapshot = database.put_snapshot(
        run.run_id, "features:v1", {"computed_fields": ["in_degree", "out_degree"]}
    )
    assert database.get_snapshot(run.run_id, "features:v1") == snapshot
    with pytest.raises(ValueError):
        database.put_snapshot(run.run_id, "invalid", {"score": float("nan")})
    with pytest.raises(IdempotencyConflictError, match="different payload"):
        database.put_snapshot(
            run.run_id,
            "features:v1",
            {"computed_fields": ["tampered"]},
            replace=True,
        )


def test_tool_completion_persists_result_and_transition_atomically(database: Database) -> None:
    run = _new_run(database)
    first = database.complete_tool(
        run.run_id,
        "inspect_dataset",
        {"ok": True, "n_nodes": 2248},
        expected_state=RunState.CREATED,
        target_state=RunState.VALIDATED,
        idempotency_key="dataset-v1",
    )
    assert first.idempotent_replay is False
    assert first.run.status is RunState.VALIDATED

    replay = database.complete_tool(
        run.run_id,
        "inspect_dataset",
        {"n_nodes": 2248, "ok": True},
        expected_state=RunState.CREATED,
        target_state=RunState.VALIDATED,
        idempotency_key="dataset-v1",
    )
    assert replay.idempotent_replay is True
    assert replay.run.status is RunState.VALIDATED

    with pytest.raises(IdempotencyConflictError):
        database.complete_tool(
            run.run_id,
            "inspect_dataset",
            {"ok": False},
            expected_state=RunState.CREATED,
            target_state=RunState.VALIDATED,
            idempotency_key="dataset-v1",
        )


def test_in_memory_database_keeps_schema_between_operations() -> None:
    with Database(":memory:") as database:
        run = database.create_run("bundled-v1", "demo")
        assert database.require_run(run.run_id).run_id == run.run_id


def test_file_database_reopens_migrations_with_required_pragmas(tmp_path) -> None:
    path = tmp_path / "nested" / "aml-agent.sqlite3"
    with Database(path) as database:
        run = database.create_run("bundled-v1", "demo")
        with database.transaction(immediate=False) as connection:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000

    with Database(path) as reopened:
        assert reopened.require_run(run.run_id).dataset_id == "bundled-v1"
