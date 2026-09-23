"""Normalize user CSV transactions into the canonical three-table dataset."""

from __future__ import annotations

import io
import re
import shutil
from pathlib import Path
from uuid import uuid4

import pandas as pd

from aml_agent.analytics.validation import DatasetValidationError, validate_dataset

REQUIRED_COLUMNS = ("src", "dst", "date", "sum_kzt")
GID_PATTERN = re.compile(r"^(0|[1-9][0-9]{0,18})$")
MAX_GID = 2**63 - 1


def import_csv_dataset(
    files: list[tuple[str, bytes]], seed_gids: list[str], target: Path
) -> dict[str, object]:
    if not files:
        raise DatasetValidationError("At least one CSV file is required.")
    seeds = _normalize_gids(pd.Series(seed_gids, dtype="string"), "seed_gids")
    if seeds.empty:
        raise DatasetValidationError("At least one seed GID is required.")
    if seeds.duplicated().any():
        raise DatasetValidationError("Seed GIDs must be unique.")

    frames: list[pd.DataFrame] = []
    for filename, content in files:
        if not filename.lower().endswith(".csv"):
            raise DatasetValidationError("Only .csv transaction files are supported.")
        try:
            frame = pd.read_csv(
                io.BytesIO(content), dtype="string", keep_default_na=False, encoding="utf-8-sig"
            )
        except (UnicodeError, pd.errors.ParserError) as exc:
            raise DatasetValidationError("A transaction file is not valid UTF-8 CSV.") from exc
        if tuple(frame.columns) != REQUIRED_COLUMNS:
            raise DatasetValidationError(
                "CSV columns must be exactly: src,dst,date,sum_kzt."
            )
        if frame.empty:
            raise DatasetValidationError("Transaction CSV files cannot be empty.")
        frames.append(frame)

    raw = pd.concat(frames, ignore_index=True)
    transactions = pd.DataFrame(
        {
            "src": _normalize_gids(raw["src"], "transactions.src"),
            "dst": _normalize_gids(raw["dst"], "transactions.dst"),
            "date": raw["date"].astype("string").str.strip(),
            "sum_kzt": pd.to_numeric(raw["sum_kzt"].str.strip(), errors="coerce"),
        }
    )
    duplicate_count = int(transactions.duplicated(list(REQUIRED_COLUMNS)).sum())
    seed_set = set(int(value) for value in seeds)
    known = set(transactions["src"].tolist()) | set(transactions["dst"].tolist())
    missing_seeds = sorted(seed_set - known)
    if missing_seeds:
        preview = ", ".join(str(value) for value in missing_seeds[:5])
        raise DatasetValidationError(f"Seed GIDs are absent from transactions: {preview}.")

    depths: dict[int, int] = {gid: 0 for gid in seed_set}
    frontier = set(seed_set)
    for depth in range(1, 5):
        destinations = set(transactions.loc[transactions["src"].isin(frontier), "dst"].tolist())
        frontier = destinations - set(depths)
        depths.update({gid: depth for gid in frontier})

    source_depth = transactions["src"].map(depths)
    included = transactions.loc[source_depth.notna() & source_depth.lt(4)].copy()
    if included.empty:
        raise DatasetValidationError(
            "Selected seeds have no outgoing transactions within four hops."
        )
    included["depth"] = included["src"].map(depths).astype("int64") + 1

    discovered = set(included["src"].tolist()) | set(included["dst"].tolist()) | seed_set
    nodes = pd.DataFrame(
        {
            "gid": sorted(discovered),
            "depth": [depths[gid] for gid in sorted(discovered)],
            "is_seed": [gid in seed_set for gid in sorted(discovered)],
        }
    )
    edges = (
        included.groupby(["src", "dst"], as_index=False, sort=True)
        .agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"), depth=("depth", "min"))
        .astype({"src": "int64", "dst": "int64", "n_tx": "int64", "depth": "int64"})
    )
    canonical_transactions = included.loc[:, REQUIRED_COLUMNS]
    dataset = validate_dataset(nodes, edges, canonical_transactions)
    if len(dataset.nodes) < 20:
        raise DatasetValidationError(
            "The selected seed network must contain at least 20 clients for the review queue."
        )

    staging = target.parent / f".{target.name}-{uuid4().hex}.tmp"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        dataset.nodes.to_parquet(staging / "nodes.parquet", index=False)
        dataset.edges.to_parquet(staging / "edges.parquet", index=False)
        dataset.transactions.to_parquet(staging / "transactions.parquet", index=False)
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    dates = dataset.transactions["date"]
    warnings = []
    if duplicate_count:
        warnings.append(
            f"Обнаружено {duplicate_count} полных дубликатов строк; "
            "без transaction_id их нельзя безопасно удалить."
        )
    return {
        "n_files": len(files),
        "n_transactions": len(dataset.transactions),
        "n_nodes": len(dataset.nodes),
        "n_edges": len(dataset.edges),
        "n_seed": int(dataset.nodes["is_seed"].sum()),
        "period_start": dates.min().date().isoformat(),
        "period_end": dates.max().date().isoformat(),
        "warnings": warnings,
    }


def _normalize_gids(values: pd.Series, label: str) -> pd.Series:
    text = values.astype("string").str.strip()
    invalid = ~text.map(lambda value: bool(GID_PATTERN.fullmatch(str(value))))
    if invalid.any():
        raise DatasetValidationError(f"{label} must contain canonical decimal int64 GIDs.")
    numbers = text.map(int)
    if (numbers > MAX_GID).any():
        raise DatasetValidationError(f"{label} contains a value outside int64 range.")
    return numbers.astype("int64")
