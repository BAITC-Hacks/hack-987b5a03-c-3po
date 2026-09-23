"""SQLite-backed repositories for runs, events, cases, and artifacts."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from aml_agent.safety import is_safe_review_case_title

from ._json import dumps as json_dumps
from ._json import loads as json_loads
from .models import (
    ALLOWED_ARTIFACT_NAMES,
    MANDATORY_ARTIFACT_NAMES,
    AgentEvent,
    AnalysisRun,
    ArtifactRecord,
    CaseCreator,
    EventKind,
    ReviewCase,
    ReviewCaseCreateResult,
    ReviewCaseStatus,
    RunMode,
    RunState,
    SnapshotRecord,
    ToolCompletion,
    ToolResultRecord,
    VerificationStatus,
)


class StorageError(RuntimeError):
    """Base class for persistence errors safe for application handling."""


class RecordNotFoundError(StorageError):
    """Raised when a requested storage record does not exist."""


class InvalidStateTransitionError(StorageError):
    """Raised when a run or review case transition violates its state machine."""


class IdempotencyConflictError(StorageError):
    """Raised when an idempotency key is reused with different immutable data."""


class ValidationError(StorageError):
    """Raised when a value is unsafe or violates a storage contract."""


_UNSET = object()
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SYMBOLIC_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GID = re.compile(r"^[0-9]+$")


ALLOWED_RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.VALIDATED}),
    RunState.VALIDATED: frozenset({RunState.GRAPH_READY}),
    RunState.GRAPH_READY: frozenset({RunState.ANALYZED}),
    RunState.ANALYZED: frozenset({RunState.CLUSTERED}),
    RunState.CLUSTERED: frozenset({RunState.CLASSIFIED}),
    RunState.CLASSIFIED: frozenset({RunState.RANKED}),
    RunState.RANKED: frozenset({RunState.CASE_CREATED, RunState.VERIFICATION_FAILED}),
    RunState.CASE_CREATED: frozenset({RunState.EXPORTED, RunState.VERIFICATION_FAILED}),
    RunState.EXPORTED: frozenset({RunState.VERIFIED, RunState.VERIFICATION_FAILED}),
    RunState.VERIFIED: frozenset({RunState.COMPLETED}),
    RunState.COMPLETED: frozenset(),
    RunState.VERIFICATION_FAILED: frozenset(),
    RunState.FAILED: frozenset(),
}

ALLOWED_CASE_TRANSITIONS: dict[ReviewCaseStatus, frozenset[ReviewCaseStatus]] = {
    ReviewCaseStatus.READY_FOR_REVIEW: frozenset(
        {ReviewCaseStatus.IN_REVIEW, ReviewCaseStatus.CLOSED}
    ),
    ReviewCaseStatus.IN_REVIEW: frozenset({ReviewCaseStatus.CLOSED}),
    ReviewCaseStatus.CLOSED: frozenset(),
}


def _sql_values(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


_RUN_STATE_SQL = _sql_values([item.value for item in RunState])
_RUN_MODE_SQL = _sql_values([item.value for item in RunMode])
_VERIFICATION_SQL = _sql_values([item.value for item in VerificationStatus])
_EVENT_KIND_SQL = _sql_values([item.value for item in EventKind])
_CASE_STATUS_SQL = _sql_values([item.value for item in ReviewCaseStatus])
_CASE_CREATOR_SQL = _sql_values([item.value for item in CaseCreator])
_ARTIFACT_NAME_SQL = _sql_values(sorted(ALLOWED_ARTIFACT_NAMES))


_MIGRATION_1 = f"""
CREATE TABLE analysis_runs (
    run_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    dataset_sha256 TEXT,
    mode TEXT NOT NULL CHECK (mode IN ({_RUN_MODE_SQL})),
    status TEXT NOT NULL CHECK (status IN ({_RUN_STATE_SQL})),
    ruleset_version TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    warning_count INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
    verification_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (verification_status IN ({_VERIFICATION_SQL})),
    verification_payload_json TEXT NOT NULL DEFAULT '{{}}',
    metadata_json TEXT NOT NULL DEFAULT '{{}}',
    previous_response_id TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    CHECK (status NOT IN ('verified', 'completed') OR verification_status = 'passed'),
    CHECK (status != 'verification_failed' OR verification_status = 'failed')
);

CREATE TABLE agent_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    kind TEXT NOT NULL CHECK (kind IN ({_EVENT_KIND_SQL})),
    tool_name TEXT,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{{}}',
    created_at TEXT NOT NULL,
    UNIQUE (run_id, sequence)
);

CREATE INDEX idx_agent_events_run_sequence
    ON agent_events(run_id, sequence);

CREATE TABLE tool_results (
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, tool_name, idempotency_key)
);

CREATE TABLE analytical_snapshots (
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, name)
);

CREATE TABLE review_cases (
    case_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({_CASE_STATUS_SQL})),
    target_gids_json TEXT NOT NULL,
    target_snapshot_sha256 TEXT NOT NULL,
    created_by TEXT NOT NULL CHECK (created_by IN ({_CASE_CREATOR_SQL})),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (name IN ({_ARTIFACT_NAME_SQL})),
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    row_count INTEGER CHECK (row_count IS NULL OR row_count >= 0),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (run_id, name),
    UNIQUE (run_id, relative_path)
);

CREATE INDEX idx_artifacts_run_name ON artifacts(run_id, name);
"""

_MIGRATIONS: tuple[tuple[int, str], ...] = ((1, _MIGRATION_1),)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValidationError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _normalize_uuid(value: str | UUID | None = None) -> str:
    try:
        return str(uuid4() if value is None else UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("identifier must be a valid UUID") from exc


def _coerce_enum[EnumT](enum_type: type[EnumT], value: EnumT | str, field: str) -> EnumT:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"invalid {field}: {value!r}") from exc


def _validate_mapping(value: Mapping[str, Any] | None, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field} must be a JSON object")
    normalized = dict(value)
    json_dumps(normalized)
    return normalized


def _validate_logical_id(value: str, field: str = "logical identifier") -> str:
    if not isinstance(value, str) or not _LOGICAL_ID.fullmatch(value):
        raise ValidationError(f"{field} contains unsafe characters")
    return value


def _validate_symbolic_name(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SYMBOLIC_NAME.fullmatch(value):
        raise ValidationError(f"{field} must be a lower-case symbolic name")
    return value


def _validate_sha256(value: str) -> str:
    normalized = value.lower()
    if not _SHA256.fullmatch(normalized):
        raise ValidationError("sha256 must contain exactly 64 hexadecimal characters")
    return normalized


class Database:
    """Small repository facade with one transaction per public mutation.

    File-backed databases use WAL mode.  ``:memory:`` is also supported for
    tests through a private shared-cache database kept alive by an anchor
    connection.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_ms: int = 5_000,
        auto_initialize: bool = True,
    ) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = Path(path) if str(path) != ":memory:" else None
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._anchor: sqlite3.Connection | None = None
        if self.path is None:
            self._dsn = f"file:aml_agent_{uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
            self._anchor = self._connect()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._dsn = str(self.path)
            self._uri = False
        if auto_initialize:
            self.initialize()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._dsn,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
            uri=self._uri,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, sql in _MIGRATIONS:
                if version in applied:
                    continue
                applied_at = _format_datetime(utc_now()).replace("'", "''")
                try:
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + sql
                        + "\n"
                        + (
                            "INSERT INTO schema_migrations(version, applied_at) "
                            f"VALUES ({version}, '{applied_at}');\n"
                        )
                        + "COMMIT;"
                    )
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Yield a configured connection inside an explicit transaction."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_run(
        self,
        dataset_id: str,
        mode: RunMode | str,
        *,
        run_id: str | UUID | None = None,
        dataset_sha256: str | None = None,
        ruleset_version: str = "v1",
        model: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AnalysisRun:
        normalized_run_id = _normalize_uuid(run_id)
        normalized_mode = _coerce_enum(RunMode, mode, "run mode")
        dataset_id = _validate_logical_id(dataset_id, "dataset_id")
        ruleset_version = _validate_logical_id(ruleset_version, "ruleset_version")
        if dataset_sha256 is not None:
            dataset_sha256 = _validate_sha256(dataset_sha256)
        if model is not None and (not isinstance(model, str) or not 1 <= len(model) <= 120):
            raise ValidationError("model must be between 1 and 120 characters")
        metadata_json = json_dumps(_validate_mapping(metadata, "metadata"))
        now = _format_datetime(utc_now())
        try:
            with self.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO analysis_runs(
                        run_id, dataset_id, dataset_sha256, mode, status,
                        ruleset_version, model, created_at, updated_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_run_id,
                        dataset_id,
                        dataset_sha256,
                        normalized_mode.value,
                        RunState.CREATED.value,
                        ruleset_version,
                        model,
                        now,
                        now,
                        metadata_json,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise IdempotencyConflictError(f"run {normalized_run_id} already exists") from exc
        return self.require_run(normalized_run_id)

    def get_run(self, run_id: str | UUID) -> AnalysisRun | None:
        normalized = _normalize_uuid(run_id)
        with self.transaction(immediate=False) as connection:
            row = connection.execute(
                "SELECT * FROM analysis_runs WHERE run_id = ?", (normalized,)
            ).fetchone()
        return None if row is None else self._run_from_row(row)

    def require_run(self, run_id: str | UUID) -> AnalysisRun:
        run = self.get_run(run_id)
        if run is None:
            raise RecordNotFoundError(f"run {_normalize_uuid(run_id)} does not exist")
        return run

    def update_run(
        self,
        run_id: str | UUID,
        *,
        dataset_sha256: str | None | object = _UNSET,
        model: str | None | object = _UNSET,
        warning_count: int | object = _UNSET,
        warning_delta: int = 0,
        metadata: Mapping[str, Any] | object = _UNSET,
        previous_response_id: str | None | object = _UNSET,
    ) -> AnalysisRun:
        normalized = _normalize_uuid(run_id)
        if warning_count is not _UNSET and warning_delta:
            raise ValidationError("warning_count and warning_delta cannot be used together")
        with self.transaction() as connection:
            row = self._require_run_row(connection, normalized)
            if RunState(row["status"]) is RunState.COMPLETED:
                raise InvalidStateTransitionError("completed runs are immutable")

            assignments: list[str] = []
            parameters: list[Any] = []
            if dataset_sha256 is not _UNSET:
                normalized_hash = (
                    None if dataset_sha256 is None else _validate_sha256(str(dataset_sha256))
                )
                current_hash = row["dataset_sha256"]
                if current_hash is not None and normalized_hash != current_hash:
                    raise IdempotencyConflictError("dataset_sha256 is immutable once recorded")
                assignments.append("dataset_sha256 = ?")
                parameters.append(normalized_hash)
            if model is not _UNSET:
                if model is not None and (not isinstance(model, str) or not 1 <= len(model) <= 120):
                    raise ValidationError("model must be between 1 and 120 characters")
                assignments.append("model = ?")
                parameters.append(model)
            if warning_count is not _UNSET:
                if not isinstance(warning_count, int) or warning_count < 0:
                    raise ValidationError("warning_count must be a non-negative integer")
                assignments.append("warning_count = ?")
                parameters.append(warning_count)
            elif warning_delta:
                if not isinstance(warning_delta, int) or row["warning_count"] + warning_delta < 0:
                    raise ValidationError("warning_delta would produce an invalid count")
                assignments.append("warning_count = warning_count + ?")
                parameters.append(warning_delta)
            if metadata is not _UNSET:
                assignments.append("metadata_json = ?")
                parameters.append(json_dumps(_validate_mapping(metadata, "metadata")))
            if previous_response_id is not _UNSET:
                if previous_response_id is not None and (
                    not isinstance(previous_response_id, str)
                    or not 1 <= len(previous_response_id) <= 255
                ):
                    raise ValidationError("previous_response_id has an invalid length")
                assignments.append("previous_response_id = ?")
                parameters.append(previous_response_id)
            if assignments:
                assignments.extend(["updated_at = ?", "revision = revision + 1"])
                parameters.extend([_format_datetime(utc_now()), normalized])
                connection.execute(
                    f"UPDATE analysis_runs SET {', '.join(assignments)} WHERE run_id = ?",
                    parameters,
                )
            updated = self._require_run_row(connection, normalized)
        return self._run_from_row(updated)

    def transition_run(
        self,
        run_id: str | UUID,
        target: RunState | str,
        *,
        expected_state: RunState | str | None = None,
    ) -> AnalysisRun:
        normalized = _normalize_uuid(run_id)
        target_state = _coerce_enum(RunState, target, "run state")
        expected = (
            None if expected_state is None else _coerce_enum(RunState, expected_state, "run state")
        )
        with self.transaction() as connection:
            row = self._require_run_row(connection, normalized)
            current = RunState(row["status"])
            if expected is not None and current is not expected and current is not target_state:
                raise InvalidStateTransitionError(
                    f"run is {current.value}, expected {expected.value}"
                )
            updated = self._transition_in_connection(connection, row, target_state)
        return self._run_from_row(updated)

    def set_verification_status(
        self,
        run_id: str | UUID,
        status: VerificationStatus | str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> AnalysisRun:
        normalized = _normalize_uuid(run_id)
        verification = _coerce_enum(VerificationStatus, status, "verification status")
        payload_json = json_dumps(_validate_mapping(payload, "verification payload"))
        with self.transaction() as connection:
            row = self._require_run_row(connection, normalized)
            self._require_mutable_run(row)
            current_status = VerificationStatus(row["verification_status"])
            current_state = RunState(row["status"])
            if verification is VerificationStatus.PENDING:
                if current_status is not VerificationStatus.PENDING:
                    raise InvalidStateTransitionError("verification cannot be reset to pending")
                connection.execute(
                    """
                    UPDATE analysis_runs
                    SET verification_payload_json = ?, updated_at = ?, revision = revision + 1
                    WHERE run_id = ?
                    """,
                    (payload_json, _format_datetime(utc_now()), normalized),
                )
            else:
                target = (
                    RunState.VERIFIED
                    if verification is VerificationStatus.PASSED
                    else RunState.VERIFICATION_FAILED
                )
                if current_state is target and current_status is verification:
                    connection.execute(
                        """
                        UPDATE analysis_runs
                        SET verification_payload_json = ?, updated_at = ?, revision = revision + 1
                        WHERE run_id = ?
                        """,
                        (payload_json, _format_datetime(utc_now()), normalized),
                    )
                else:
                    row = self._transition_in_connection(connection, row, target)
                    connection.execute(
                        """
                        UPDATE analysis_runs
                        SET verification_payload_json = ?, updated_at = ?, revision = revision + 1
                        WHERE run_id = ?
                        """,
                        (payload_json, _format_datetime(utc_now()), normalized),
                    )
            updated = self._require_run_row(connection, normalized)
        return self._run_from_row(updated)

    def record_verification(
        self,
        run_id: str | UUID,
        *,
        passed: bool,
        checks: Mapping[str, Any] | None = None,
    ) -> AnalysisRun:
        return self.set_verification_status(
            run_id,
            VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
            payload=checks,
        )

    def complete_run(self, run_id: str | UUID) -> AnalysisRun:
        return self.transition_run(run_id, RunState.COMPLETED, expected_state=RunState.VERIFIED)

    def append_event(
        self,
        run_id: str | UUID,
        kind: EventKind | str,
        summary: str,
        *,
        tool_name: str | None = None,
        payload: Mapping[str, Any] | None = None,
        event_id: str | UUID | None = None,
    ) -> AgentEvent:
        normalized_run_id = _normalize_uuid(run_id)
        normalized_event_id = _normalize_uuid(event_id)
        normalized_kind = _coerce_enum(EventKind, kind, "event kind")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1_000:
            raise ValidationError("event summary must contain 1 to 1000 characters")
        if tool_name is not None:
            tool_name = _validate_symbolic_name(tool_name, "tool_name")
        payload_json = json_dumps(_validate_mapping(payload, "event payload"))
        created_at = _format_datetime(utc_now())
        with self.transaction() as connection:
            run_row = self._require_run_row(connection, normalized_run_id)
            self._require_mutable_run(run_row)
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_events WHERE run_id = ?",
                    (normalized_run_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO agent_events(
                    event_id, run_id, sequence, kind, tool_name, summary,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_event_id,
                    normalized_run_id,
                    sequence,
                    normalized_kind.value,
                    tool_name,
                    summary.strip(),
                    payload_json,
                    created_at,
                ),
            )
            if normalized_kind is EventKind.WARNING:
                connection.execute(
                    """
                    UPDATE analysis_runs
                    SET warning_count = warning_count + 1, updated_at = ?, revision = revision + 1
                    WHERE run_id = ?
                    """,
                    (created_at, normalized_run_id),
                )
            row = connection.execute(
                "SELECT * FROM agent_events WHERE event_id = ?", (normalized_event_id,)
            ).fetchone()
        return self._event_from_row(row)

    def list_events(
        self,
        run_id: str | UUID,
        *,
        after_sequence: int = 0,
        limit: int = 1_000,
    ) -> list[AgentEvent]:
        normalized = _normalize_uuid(run_id)
        if after_sequence < 0:
            raise ValidationError("after_sequence cannot be negative")
        if not 1 <= limit <= 10_000:
            raise ValidationError("event limit must be between 1 and 10000")
        with self.transaction(immediate=False) as connection:
            self._require_run_row(connection, normalized)
            rows = connection.execute(
                """
                SELECT * FROM agent_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (normalized, after_sequence, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def put_tool_result(
        self,
        run_id: str | UUID,
        tool_name: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str = "default",
    ) -> ToolResultRecord:
        normalized = _normalize_uuid(run_id)
        tool_name = _validate_symbolic_name(tool_name, "tool_name")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 255:
            raise ValidationError("idempotency_key must contain 1 to 255 characters")
        payload_json = json_dumps(_validate_mapping(payload, "tool payload"))
        now = _format_datetime(utc_now())
        with self.transaction() as connection:
            run_row = self._require_run_row(connection, normalized)
            self._require_mutable_run(run_row)
            row, _ = self._put_tool_result_in_connection(
                connection,
                normalized,
                tool_name,
                idempotency_key,
                payload_json,
                now,
            )
        return self._tool_result_from_row(row)

    store_tool_payload = put_tool_result

    def complete_tool(
        self,
        run_id: str | UUID,
        tool_name: str,
        payload: Mapping[str, Any],
        *,
        expected_state: RunState | str,
        target_state: RunState | str,
        idempotency_key: str = "default",
    ) -> ToolCompletion:
        """Persist a tool result and its state transition in one transaction."""

        normalized = _normalize_uuid(run_id)
        tool_name = _validate_symbolic_name(tool_name, "tool_name")
        expected = _coerce_enum(RunState, expected_state, "run state")
        target = _coerce_enum(RunState, target_state, "run state")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 255:
            raise ValidationError("idempotency_key must contain 1 to 255 characters")
        payload_json = json_dumps(_validate_mapping(payload, "tool payload"))
        now = _format_datetime(utc_now())
        with self.transaction() as connection:
            run_row = self._require_run_row(connection, normalized)
            self._require_mutable_run(run_row)
            current = RunState(run_row["status"])
            if current not in {expected, target}:
                raise InvalidStateTransitionError(
                    f"run is {current.value}, expected {expected.value} or {target.value}"
                )
            result_row, replay = self._put_tool_result_in_connection(
                connection,
                normalized,
                tool_name,
                idempotency_key,
                payload_json,
                now,
            )
            updated_run_row = self._transition_in_connection(connection, run_row, target)
        return ToolCompletion(
            tool_result=self._tool_result_from_row(result_row),
            run=self._run_from_row(updated_run_row),
            idempotent_replay=replay,
        )

    def get_tool_result(
        self,
        run_id: str | UUID,
        tool_name: str,
        *,
        idempotency_key: str = "default",
    ) -> ToolResultRecord | None:
        normalized = _normalize_uuid(run_id)
        tool_name = _validate_symbolic_name(tool_name, "tool_name")
        with self.transaction(immediate=False) as connection:
            row = connection.execute(
                """
                SELECT * FROM tool_results
                WHERE run_id = ? AND tool_name = ? AND idempotency_key = ?
                """,
                (normalized, tool_name, idempotency_key),
            ).fetchone()
        return None if row is None else self._tool_result_from_row(row)

    get_tool_payload = get_tool_result

    def get_recorded_tool_result(
        self, run_id: str | UUID, tool_name: str
    ) -> ToolResultRecord | None:
        """Read the accepted result for a run/tool without reconstructing its argument hash."""

        normalized = _normalize_uuid(run_id)
        tool_name = _validate_symbolic_name(tool_name, "tool_name")
        with self.transaction(immediate=False) as connection:
            rows = connection.execute(
                """
                SELECT * FROM tool_results
                WHERE run_id = ? AND tool_name = ?
                ORDER BY created_at ASC LIMIT 2
                """,
                (normalized, tool_name),
            ).fetchall()
        if len(rows) > 1:
            raise IdempotencyConflictError("multiple results exist for one run/tool")
        return None if not rows else self._tool_result_from_row(rows[0])

    def put_snapshot(
        self,
        run_id: str | UUID,
        name: str,
        payload: Mapping[str, Any],
        *,
        replace: bool = False,
    ) -> SnapshotRecord:
        normalized = _normalize_uuid(run_id)
        if not isinstance(name, str) or not _SNAPSHOT_NAME.fullmatch(name):
            raise ValidationError("snapshot name contains unsafe characters")
        payload_json = json_dumps(_validate_mapping(payload, "snapshot payload"))
        now = _format_datetime(utc_now())
        with self.transaction() as connection:
            run_row = self._require_run_row(connection, normalized)
            self._require_mutable_run(run_row)
            existing = connection.execute(
                "SELECT * FROM analytical_snapshots WHERE run_id = ? AND name = ?",
                (normalized, name),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] == payload_json:
                    return self._snapshot_from_row(existing)
                raise IdempotencyConflictError(
                    "snapshot already exists with a different payload"
                )
            else:
                connection.execute(
                    """
                    INSERT INTO analytical_snapshots(
                        run_id, name, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (normalized, name, payload_json, now, now),
                )
            row = connection.execute(
                "SELECT * FROM analytical_snapshots WHERE run_id = ? AND name = ?",
                (normalized, name),
            ).fetchone()
        return self._snapshot_from_row(row)

    def get_snapshot(self, run_id: str | UUID, name: str) -> SnapshotRecord | None:
        normalized = _normalize_uuid(run_id)
        if not isinstance(name, str) or not _SNAPSHOT_NAME.fullmatch(name):
            raise ValidationError("snapshot name contains unsafe characters")
        with self.transaction(immediate=False) as connection:
            row = connection.execute(
                "SELECT * FROM analytical_snapshots WHERE run_id = ? AND name = ?",
                (normalized, name),
            ).fetchone()
        return None if row is None else self._snapshot_from_row(row)

    def create_review_case(
        self,
        run_id: str | UUID,
        title: str,
        target_gids: Sequence[str],
        *,
        created_by: CaseCreator | str = CaseCreator.AGENT,
        case_id: str | UUID | None = None,
    ) -> ReviewCaseCreateResult:
        normalized_run_id = _normalize_uuid(run_id)
        normalized_case_id = _normalize_uuid(case_id)
        creator = _coerce_enum(CaseCreator, created_by, "case creator")
        if not isinstance(title, str) or not 3 <= len(title.strip()) <= 120:
            raise ValidationError("case title must contain 3 to 120 characters")
        if not is_safe_review_case_title(title):
            raise ValidationError(
                "case title must be one of the fixed reviewed MVP titles"
            )
        if isinstance(target_gids, (str, bytes)) or not target_gids:
            raise ValidationError("target_gids must be a non-empty sequence")
        normalized_targets = tuple(target_gids)
        if any(not isinstance(gid, str) or not _GID.fullmatch(gid) for gid in normalized_targets):
            raise ValidationError("every target GID must be a decimal string")
        if len(set(normalized_targets)) != len(normalized_targets):
            raise ValidationError("target GIDs must be unique")
        targets_json = json_dumps(normalized_targets)
        from hashlib import sha256

        snapshot_hash = sha256(targets_json.encode("utf-8")).hexdigest()
        now = _format_datetime(utc_now())
        with self.transaction() as connection:
            run_row = self._require_run_row(connection, normalized_run_id)
            existing = connection.execute(
                "SELECT * FROM review_cases WHERE run_id = ?", (normalized_run_id,)
            ).fetchone()
            if existing is not None:
                same_request = (
                    existing["title"] == title.strip()
                    and existing["target_snapshot_sha256"] == snapshot_hash
                    and existing["created_by"] == creator.value
                )
                if not same_request:
                    raise IdempotencyConflictError(
                        "a different review case already exists for this run"
                    )
                return ReviewCaseCreateResult(
                    case=self._review_case_from_row(existing), idempotent_replay=True
                )
            if RunState(run_row["status"]) is not RunState.RANKED:
                raise InvalidStateTransitionError(
                    "a review case can only be created for a ranked run"
                )
            connection.execute(
                """
                INSERT INTO review_cases(
                    case_id, run_id, title, status, target_gids_json,
                    target_snapshot_sha256, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_case_id,
                    normalized_run_id,
                    title.strip(),
                    ReviewCaseStatus.READY_FOR_REVIEW.value,
                    targets_json,
                    snapshot_hash,
                    creator.value,
                    now,
                    now,
                ),
            )
            self._transition_in_connection(connection, run_row, RunState.CASE_CREATED)
            case_row = connection.execute(
                "SELECT * FROM review_cases WHERE case_id = ?", (normalized_case_id,)
            ).fetchone()
        return ReviewCaseCreateResult(
            case=self._review_case_from_row(case_row), idempotent_replay=False
        )

    def get_review_case(self, case_id: str | UUID) -> ReviewCase | None:
        normalized = _normalize_uuid(case_id)
        with self.transaction(immediate=False) as connection:
            row = connection.execute(
                "SELECT * FROM review_cases WHERE case_id = ?", (normalized,)
            ).fetchone()
        return None if row is None else self._review_case_from_row(row)

    def get_review_case_for_run(self, run_id: str | UUID) -> ReviewCase | None:
        normalized = _normalize_uuid(run_id)
        with self.transaction(immediate=False) as connection:
            row = connection.execute(
                "SELECT * FROM review_cases WHERE run_id = ?", (normalized,)
            ).fetchone()
        return None if row is None else self._review_case_from_row(row)

    def update_review_case_status(
        self, case_id: str | UUID, target: ReviewCaseStatus | str
    ) -> ReviewCase:
        normalized = _normalize_uuid(case_id)
        target_status = _coerce_enum(ReviewCaseStatus, target, "review case status")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM review_cases WHERE case_id = ?", (normalized,)
            ).fetchone()
            if row is None:
                raise RecordNotFoundError(f"review case {normalized} does not exist")
            current = ReviewCaseStatus(row["status"])
            if current is not target_status:
                if target_status not in ALLOWED_CASE_TRANSITIONS[current]:
                    raise InvalidStateTransitionError(
                        "review case cannot transition "
                        f"from {current.value} to {target_status.value}"
                    )
                connection.execute(
                    "UPDATE review_cases SET status = ?, updated_at = ? WHERE case_id = ?",
                    (target_status.value, _format_datetime(utc_now()), normalized),
                )
            updated = connection.execute(
                "SELECT * FROM review_cases WHERE case_id = ?", (normalized,)
            ).fetchone()
        return self._review_case_from_row(updated)

    def register_artifact(
        self,
        run_id: str | UUID,
        *,
        name: str,
        relative_path: str,
        sha256: str,
        row_count: int | None,
        size_bytes: int,
        artifact_id: str | UUID | None = None,
    ) -> ArtifactRecord:
        normalized_run_id = _normalize_uuid(run_id)
        if name not in ALLOWED_ARTIFACT_NAMES:
            raise ValidationError(f"artifact name is not allowed: {name!r}")
        if "\\" in relative_path or PurePosixPath(relative_path).parts != (
            normalized_run_id,
            name,
        ):
            raise ValidationError("artifact relative_path must be '<run_id>/<allowed-name>'")
        normalized_hash = _validate_sha256(sha256)
        if name.endswith(".csv") and row_count is None:
            raise ValidationError("CSV artifacts require row_count metadata")
        if row_count is not None and (not isinstance(row_count, int) or row_count < 0):
            raise ValidationError("row_count must be a non-negative integer or null")
        if not isinstance(size_bytes, int) or size_bytes < 0:
            raise ValidationError("size_bytes must be a non-negative integer")
        normalized_artifact_id = _normalize_uuid(artifact_id)
        now = _format_datetime(utc_now())
        with self.transaction() as connection:
            run_row = self._require_run_row(connection, normalized_run_id)
            self._require_mutable_run(run_row)
            existing = connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? AND name = ?",
                (normalized_run_id, name),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        artifact_id, run_id, name, relative_path, sha256,
                        row_count, size_bytes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_artifact_id,
                        normalized_run_id,
                        name,
                        relative_path,
                        normalized_hash,
                        row_count,
                        size_bytes,
                        now,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE artifacts
                    SET relative_path = ?, sha256 = ?, row_count = ?, size_bytes = ?, updated_at = ?
                    WHERE run_id = ? AND name = ?
                    """,
                    (
                        relative_path,
                        normalized_hash,
                        row_count,
                        size_bytes,
                        now,
                        normalized_run_id,
                        name,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? AND name = ?",
                (normalized_run_id, name),
            ).fetchone()
        return self._artifact_from_row(row)

    add_artifact = register_artifact

    def get_artifact(self, run_id: str | UUID, name: str) -> ArtifactRecord | None:
        normalized = _normalize_uuid(run_id)
        if name not in ALLOWED_ARTIFACT_NAMES:
            raise ValidationError(f"artifact name is not allowed: {name!r}")
        with self.transaction(immediate=False) as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? AND name = ?",
                (normalized, name),
            ).fetchone()
        return None if row is None else self._artifact_from_row(row)

    def list_artifacts(self, run_id: str | UUID) -> list[ArtifactRecord]:
        normalized = _normalize_uuid(run_id)
        with self.transaction(immediate=False) as connection:
            self._require_run_row(connection, normalized)
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? ORDER BY name ASC", (normalized,)
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def mark_exported(self, run_id: str | UUID, *, include_audit: bool = False) -> AnalysisRun:
        normalized = _normalize_uuid(run_id)
        with self.transaction() as connection:
            row = self._require_run_row(connection, normalized)
            found = {
                item["name"]
                for item in connection.execute(
                    "SELECT name FROM artifacts WHERE run_id = ?", (normalized,)
                )
            }
            required = set(MANDATORY_ARTIFACT_NAMES)
            if include_audit:
                required.add("audit.json")
            missing = sorted(required - found)
            if missing:
                raise ValidationError(f"cannot mark run exported; missing artifacts: {missing}")
            updated = self._transition_in_connection(connection, row, RunState.EXPORTED)
        return self._run_from_row(updated)

    def _transition_in_connection(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        target: RunState,
    ) -> sqlite3.Row:
        current = RunState(row["status"])
        if current is target:
            return row
        allowed = target in ALLOWED_RUN_TRANSITIONS[current]
        if target is RunState.FAILED and current not in {RunState.COMPLETED, RunState.FAILED}:
            allowed = True
        if not allowed:
            raise InvalidStateTransitionError(
                f"run cannot transition from {current.value} to {target.value}"
            )
        verification_status = VerificationStatus(row["verification_status"])
        if target is RunState.VERIFIED:
            verification_status = VerificationStatus.PASSED
        elif target is RunState.VERIFICATION_FAILED:
            verification_status = VerificationStatus.FAILED
        now = _format_datetime(utc_now())
        connection.execute(
            """
            UPDATE analysis_runs
            SET status = ?, verification_status = ?, updated_at = ?, revision = revision + 1
            WHERE run_id = ?
            """,
            (target.value, verification_status.value, now, row["run_id"]),
        )
        return self._require_run_row(connection, row["run_id"])

    def _put_tool_result_in_connection(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        tool_name: str,
        idempotency_key: str,
        payload_json: str,
        now: str,
    ) -> tuple[sqlite3.Row, bool]:
        existing = connection.execute(
            """
            SELECT * FROM tool_results
            WHERE run_id = ? AND tool_name = ? AND idempotency_key = ?
            """,
            (run_id, tool_name, idempotency_key),
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != payload_json:
                raise IdempotencyConflictError(
                    "tool idempotency key was already used with different output"
                )
            return existing, True
        connection.execute(
            """
            INSERT INTO tool_results(
                run_id, tool_name, idempotency_key, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, tool_name, idempotency_key, payload_json, now, now),
        )
        inserted = connection.execute(
            """
            SELECT * FROM tool_results
            WHERE run_id = ? AND tool_name = ? AND idempotency_key = ?
            """,
            (run_id, tool_name, idempotency_key),
        ).fetchone()
        return inserted, False

    @staticmethod
    def _require_mutable_run(row: sqlite3.Row) -> None:
        if RunState(row["status"]) is RunState.COMPLETED:
            raise InvalidStateTransitionError("completed runs are immutable")

    @staticmethod
    def _require_run_row(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM analysis_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"run {run_id} does not exist")
        return row

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> AnalysisRun:
        return AnalysisRun(
            run_id=row["run_id"],
            dataset_id=row["dataset_id"],
            dataset_sha256=row["dataset_sha256"],
            mode=RunMode(row["mode"]),
            status=RunState(row["status"]),
            ruleset_version=row["ruleset_version"],
            model=row["model"],
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
            warning_count=int(row["warning_count"]),
            verification_status=VerificationStatus(row["verification_status"]),
            verification_payload=dict(json_loads(row["verification_payload_json"])),
            metadata=dict(json_loads(row["metadata_json"])),
            previous_response_id=row["previous_response_id"],
            revision=int(row["revision"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> AgentEvent:
        return AgentEvent(
            event_id=row["event_id"],
            run_id=row["run_id"],
            sequence=int(row["sequence"]),
            kind=EventKind(row["kind"]),
            tool_name=row["tool_name"],
            summary=row["summary"],
            payload=dict(json_loads(row["payload_json"])),
            created_at=_parse_datetime(row["created_at"]),
        )

    @staticmethod
    def _review_case_from_row(row: sqlite3.Row) -> ReviewCase:
        return ReviewCase(
            case_id=row["case_id"],
            run_id=row["run_id"],
            title=row["title"],
            status=ReviewCaseStatus(row["status"]),
            target_gids=tuple(json_loads(row["target_gids_json"])),
            created_by=CaseCreator(row["created_by"]),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            run_id=row["run_id"],
            name=row["name"],
            relative_path=row["relative_path"],
            sha256=row["sha256"],
            row_count=None if row["row_count"] is None else int(row["row_count"]),
            size_bytes=int(row["size_bytes"]),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )

    @staticmethod
    def _tool_result_from_row(row: sqlite3.Row) -> ToolResultRecord:
        return ToolResultRecord(
            run_id=row["run_id"],
            tool_name=row["tool_name"],
            idempotency_key=row["idempotency_key"],
            payload=dict(json_loads(row["payload_json"])),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> SnapshotRecord:
        return SnapshotRecord(
            run_id=row["run_id"],
            name=row["name"],
            payload=dict(json_loads(row["payload_json"])),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )


SQLiteRepository = Database
