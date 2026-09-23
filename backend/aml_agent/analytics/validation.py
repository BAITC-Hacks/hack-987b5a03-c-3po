"""Dataset loading, validation, and deterministic fingerprinting.

This module is deliberately independent from FastAPI, persistence, and the
agent runtime.  Callers receive normalized copies of the three authoritative
input tables or a precise validation error before any graph work starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from hashlib import sha256
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
from pandas.api import types as ptypes

AMOUNT_TOLERANCE_KZT: Final[float] = 0.01
MIN_TRANSACTION_KZT: Final[float] = 5_000.0

NODE_COLUMNS: Final[tuple[str, ...]] = ("gid", "depth", "is_seed")
EDGE_COLUMNS: Final[tuple[str, ...]] = (
    "src",
    "dst",
    "sum_kzt",
    "n_tx",
    "depth",
)
TRANSACTION_COLUMNS: Final[tuple[str, ...]] = ("src", "dst", "date", "sum_kzt")


class DatasetValidationError(ValueError):
    """Raised when an input bundle violates an analytical invariant."""


@dataclass(frozen=True, slots=True)
class DatasetTables:
    """Validated and normalized input tables.

    Identifier columns remain signed ``int64`` as required by the parquet and
    CSV contracts.  ``transactions.date`` is normalized to midnight while the
    source limitation (no intraday ordering) remains explicit downstream.
    """

    nodes: pd.DataFrame
    edges: pd.DataFrame
    transactions: pd.DataFrame
    sha256: str


def load_dataset(
    data_dir: str | Path,
    *,
    expected_seed_count: int | None = None,
    expected_period: tuple[str | Date, str | Date] | None = None,
) -> DatasetTables:
    """Load and validate ``nodes``, ``edges``, and ``transactions`` parquet.

    The directory is a trusted application-level path.  Agent-facing tools
    must resolve logical ``dataset_id`` values before calling this function.
    """

    directory = Path(data_dir)
    if not directory.is_dir():
        raise DatasetValidationError(f"Dataset directory does not exist: {directory}")

    frames: dict[str, pd.DataFrame] = {}
    for name in ("nodes", "edges", "transactions"):
        path = directory / f"{name}.parquet"
        if not path.is_file():
            raise DatasetValidationError(f"Required parquet file is missing: {path.name}")
        try:
            frames[name] = pd.read_parquet(path)
        except Exception as exc:  # pandas wraps missing engines and corrupt files
            raise DatasetValidationError(f"Could not read {path.name} as parquet: {exc}") from exc

    return validate_dataset(
        nodes=frames["nodes"],
        edges=frames["edges"],
        transactions=frames["transactions"],
        expected_seed_count=expected_seed_count,
        expected_period=expected_period,
    )


def validate_dataset(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    transactions: pd.DataFrame,
    *,
    expected_seed_count: int | None = None,
    expected_period: tuple[str | Date, str | Date] | None = None,
) -> DatasetTables:
    """Validate dataframes and return canonical typed copies.

    Reconciliation is strict on edge pairs and counts, and permits at most
    ``0.01`` KZT absolute floating-point drift in aggregated amounts.
    """

    normalized_nodes = _normalize_nodes(nodes)
    normalized_edges = _normalize_edges(edges)
    normalized_transactions = _normalize_transactions(transactions)

    if expected_period is not None:
        period_start, period_end = _normalize_expected_period(expected_period)
        observed_dates = normalized_transactions["date"]
        outside_period = (observed_dates < period_start) | (observed_dates > period_end)
        if outside_period.any():
            observed_start = observed_dates.min().date().isoformat()
            observed_end = observed_dates.max().date().isoformat()
            raise DatasetValidationError(
                "transactions.date falls outside the declared batch period "
                f"{period_start.date().isoformat()} through {period_end.date().isoformat()}; "
                f"observed {observed_start} through {observed_end}."
            )

    if expected_seed_count is not None:
        actual = int(normalized_nodes["is_seed"].sum())
        if actual != expected_seed_count:
            raise DatasetValidationError(
                f"Expected {expected_seed_count} seed nodes, found {actual}."
            )

    known_gids = set(normalized_nodes["gid"].tolist())
    referenced_gids = set(normalized_edges["src"].tolist()) | set(normalized_edges["dst"].tolist())
    unknown_edges = sorted(referenced_gids - known_gids)
    if unknown_edges:
        raise DatasetValidationError(
            "Edges reference GIDs absent from nodes: " + _preview(unknown_edges)
        )

    tx_gids = set(normalized_transactions["src"].tolist()) | set(
        normalized_transactions["dst"].tolist()
    )
    unknown_transactions = sorted(tx_gids - known_gids)
    if unknown_transactions:
        raise DatasetValidationError(
            "Transactions reference GIDs absent from nodes: " + _preview(unknown_transactions)
        )

    _validate_edge_reconciliation(normalized_edges, normalized_transactions)

    digest = fingerprint_dataset(normalized_nodes, normalized_edges, normalized_transactions)
    return DatasetTables(
        nodes=normalized_nodes,
        edges=normalized_edges,
        transactions=normalized_transactions,
        sha256=digest,
    )


def fingerprint_dataset(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    transactions: pd.DataFrame,
) -> str:
    """Return an order-independent SHA-256 fingerprint of normalized records."""

    digest = sha256()
    digest.update(b"aml-agent-dataset-v1\n")

    ordered_nodes = nodes.loc[:, NODE_COLUMNS].sort_values(["gid"], kind="mergesort")
    _hash_header(digest, "nodes", NODE_COLUMNS)
    for row in ordered_nodes.itertuples(index=False, name=None):
        digest.update(f"{int(row[0])}|{int(row[1])}|{int(bool(row[2]))}\n".encode("ascii"))

    ordered_edges = edges.loc[:, EDGE_COLUMNS].sort_values(["src", "dst"], kind="mergesort")
    _hash_header(digest, "edges", EDGE_COLUMNS)
    for src, dst, amount, n_tx, depth in ordered_edges.itertuples(index=False, name=None):
        digest.update(
            (f"{int(src)}|{int(dst)}|{_canonical_float(amount)}|{int(n_tx)}|{int(depth)}\n").encode(
                "ascii"
            )
        )

    ordered_transactions = transactions.loc[:, TRANSACTION_COLUMNS].sort_values(
        ["src", "dst", "date", "sum_kzt"], kind="mergesort"
    )
    _hash_header(digest, "transactions", TRANSACTION_COLUMNS)
    for src, dst, date, amount in ordered_transactions.itertuples(index=False, name=None):
        digest.update(
            (
                f"{int(src)}|{int(dst)}|{pd.Timestamp(date).date().isoformat()}|"
                f"{_canonical_float(amount)}\n"
            ).encode("ascii")
        )
    return digest.hexdigest()


def _normalize_nodes(frame: pd.DataFrame) -> pd.DataFrame:
    _require_frame("nodes", frame, NODE_COLUMNS)
    _require_non_empty("nodes", frame)
    _require_integral("nodes", frame, "gid")
    _require_integral("nodes", frame, "depth")
    if not ptypes.is_bool_dtype(frame["is_seed"].dtype):
        raise DatasetValidationError("nodes.is_seed must have boolean dtype.")

    result = frame.loc[:, NODE_COLUMNS].copy()
    _require_no_nulls("nodes", result)
    result["gid"] = _to_int64(result["gid"], "nodes.gid")
    result["depth"] = _to_int64(result["depth"], "nodes.depth")
    result["is_seed"] = result["is_seed"].astype(bool)

    if result["gid"].duplicated().any():
        duplicates = sorted(result.loc[result["gid"].duplicated(False), "gid"].unique())
        raise DatasetValidationError(
            "nodes.gid must be unique; duplicates: " + _preview(duplicates)
        )
    if not result["depth"].between(0, 4).all():
        raise DatasetValidationError("nodes.depth must be between 0 and 4 inclusive.")
    return result.sort_values("gid", kind="mergesort").reset_index(drop=True)


def _normalize_edges(frame: pd.DataFrame) -> pd.DataFrame:
    _require_frame("edges", frame, EDGE_COLUMNS)
    _require_non_empty("edges", frame)
    for column in ("src", "dst", "n_tx", "depth"):
        _require_integral("edges", frame, column)
    _require_numeric("edges", frame, "sum_kzt")

    result = frame.loc[:, EDGE_COLUMNS].copy()
    _require_no_nulls("edges", result)
    for column in ("src", "dst", "n_tx", "depth"):
        result[column] = _to_int64(result[column], f"edges.{column}")
    result["sum_kzt"] = result["sum_kzt"].astype("float64")

    if result.duplicated(["src", "dst"]).any():
        duplicates = result.loc[
            result.duplicated(["src", "dst"], keep=False), ["src", "dst"]
        ].drop_duplicates()
        pairs = [f"{src}->{dst}" for src, dst in duplicates.itertuples(index=False)]
        raise DatasetValidationError(
            "edges (src, dst) pairs must be unique; duplicates: " + _preview(pairs)
        )
    _require_finite_minimum(result["sum_kzt"], "edges.sum_kzt", MIN_TRANSACTION_KZT)
    if not (result["n_tx"] > 0).all():
        raise DatasetValidationError("edges.n_tx must be positive.")
    if not result["depth"].between(1, 4).all():
        raise DatasetValidationError("edges.depth must be between 1 and 4 inclusive.")
    return result.sort_values(["src", "dst"], kind="mergesort").reset_index(drop=True)


def _normalize_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    _require_frame("transactions", frame, TRANSACTION_COLUMNS)
    _require_non_empty("transactions", frame)
    for column in ("src", "dst"):
        _require_integral("transactions", frame, column)
    _require_numeric("transactions", frame, "sum_kzt")

    result = frame.loc[:, TRANSACTION_COLUMNS].copy()
    _require_no_nulls("transactions", result)
    result["src"] = _to_int64(result["src"], "transactions.src")
    result["dst"] = _to_int64(result["dst"], "transactions.dst")
    result["sum_kzt"] = result["sum_kzt"].astype("float64")
    _require_finite_minimum(result["sum_kzt"], "transactions.sum_kzt", MIN_TRANSACTION_KZT)
    try:
        parsed_dates = pd.to_datetime(result["date"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise DatasetValidationError("transactions.date contains an invalid date.") from exc
    if parsed_dates.isna().any():
        raise DatasetValidationError("transactions.date cannot contain null values.")
    if getattr(parsed_dates.dt, "tz", None) is not None:
        parsed_dates = parsed_dates.dt.tz_convert("UTC").dt.tz_localize(None)
    result["date"] = parsed_dates.dt.normalize()
    return result.sort_values(["src", "dst", "date", "sum_kzt"], kind="mergesort").reset_index(
        drop=True
    )


def _validate_edge_reconciliation(edges: pd.DataFrame, transactions: pd.DataFrame) -> None:
    aggregated = (
        transactions.groupby(["src", "dst"], as_index=False, sort=True)
        .agg(tx_sum_kzt=("sum_kzt", "sum"), tx_count=("sum_kzt", "size"))
        .astype({"tx_count": "int64"})
    )
    merged = edges.merge(
        aggregated,
        on=["src", "dst"],
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    pair_mismatch = merged["_merge"] != "both"
    if pair_mismatch.any():
        rows = merged.loc[pair_mismatch, ["src", "dst", "_merge"]]
        descriptions = [
            f"{int(src)}->{int(dst)} ({where})"
            for src, dst, where in rows.head(5).itertuples(index=False, name=None)
        ]
        raise DatasetValidationError(
            "edges and transactions contain different pairs: " + ", ".join(descriptions)
        )
    amount_delta = (merged["sum_kzt"] - merged["tx_sum_kzt"]).abs()
    if (amount_delta > AMOUNT_TOLERANCE_KZT).any():
        row = merged.loc[amount_delta.idxmax()]
        raise DatasetValidationError(
            "Transaction amounts do not reconcile for edge "
            f"{int(row['src'])}->{int(row['dst'])}: delta "
            f"{float(amount_delta.max()):.6f} KZT."
        )
    count_mismatch = merged["n_tx"] != merged["tx_count"]
    if count_mismatch.any():
        row = merged.loc[count_mismatch].iloc[0]
        raise DatasetValidationError(
            "Transaction count does not reconcile for edge "
            f"{int(row['src'])}->{int(row['dst'])}: expected "
            f"{int(row['n_tx'])}, found {int(row['tx_count'])}."
        )


def _normalize_expected_period(
    period: tuple[str | Date, str | Date],
) -> tuple[pd.Timestamp, pd.Timestamp]:
    if not isinstance(period, tuple) or len(period) != 2:
        raise DatasetValidationError("expected_period must contain start and end dates.")
    try:
        start = pd.Timestamp(period[0])
        end = pd.Timestamp(period[1])
    except (TypeError, ValueError) as exc:
        raise DatasetValidationError("expected_period contains an invalid date.") from exc
    if start.tzinfo is not None:
        start = start.tz_convert("UTC").tz_localize(None)
    if end.tzinfo is not None:
        end = end.tz_convert("UTC").tz_localize(None)
    start = start.normalize()
    end = end.normalize()
    if start > end:
        raise DatasetValidationError("expected_period start must not be after end.")
    return start, end


def _require_frame(name: str, frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise DatasetValidationError(f"{name} must be a pandas DataFrame.")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise DatasetValidationError(f"{name} is missing required columns: {', '.join(missing)}.")


def _require_non_empty(name: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        raise DatasetValidationError(f"{name} cannot be empty.")


def _require_no_nulls(name: str, frame: pd.DataFrame) -> None:
    null_columns = [column for column in frame.columns if frame[column].isna().any()]
    if null_columns:
        raise DatasetValidationError(f"{name} contains null values in: {', '.join(null_columns)}.")


def _require_integral(name: str, frame: pd.DataFrame, column: str) -> None:
    if not ptypes.is_integer_dtype(frame[column].dtype):
        raise DatasetValidationError(f"{name}.{column} must have an integer dtype.")


def _require_numeric(name: str, frame: pd.DataFrame, column: str) -> None:
    dtype = frame[column].dtype
    if not ptypes.is_numeric_dtype(dtype) or ptypes.is_bool_dtype(dtype):
        raise DatasetValidationError(f"{name}.{column} must have a numeric dtype.")


def _to_int64(series: pd.Series, label: str) -> pd.Series:
    info = np.iinfo(np.int64)
    if ((series < info.min) | (series > info.max)).any():
        raise DatasetValidationError(f"{label} contains a value outside int64 range.")
    return series.astype("int64")


def _require_finite_minimum(series: pd.Series, label: str, minimum: float) -> None:
    values = series.to_numpy(dtype="float64", copy=False)
    if not np.isfinite(values).all():
        raise DatasetValidationError(f"{label} must contain only finite values.")
    if (values < minimum).any():
        raise DatasetValidationError(f"{label} must be at least {minimum:.0f} KZT.")


def _canonical_float(value: float) -> str:
    number = float(value)
    if number == 0.0:
        number = 0.0
    return format(number, ".17g")


def _hash_header(digest: object, name: str, columns: tuple[str, ...]) -> None:
    digest.update(f"[{name}]|{'|'.join(columns)}\n".encode("ascii"))


def _preview(values: list[object], limit: int = 5) -> str:
    rendered = ", ".join(str(value) for value in values[:limit])
    if len(values) > limit:
        rendered += f", ... (+{len(values) - limit})"
    return rendered
