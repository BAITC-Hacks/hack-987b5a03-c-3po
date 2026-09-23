"""Atomic, byte-stable CSV export for the judging contract."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Final

import pandas as pd

NODES_ROLES_COLUMNS: Final[tuple[str, ...]] = (
    "gid",
    "role",
    "role_score",
    "cluster_id",
    "priority_score",
    "evidence",
)
CLUSTERS_COLUMNS: Final[tuple[str, ...]] = (
    "cluster_id",
    "n_nodes",
    "n_seed",
    "sum_kzt_internal",
    "top_gids",
    "hypothesis",
)
TOP_NODES_COLUMNS: Final[tuple[str, ...]] = (
    "rank",
    "gid",
    "role",
    "priority_score",
    "why",
)


def write_analytics_csvs(
    output_dir: str | Path,
    assessments: pd.DataFrame,
    clusters: pd.DataFrame,
    top_nodes: pd.DataFrame,
) -> dict[str, Path]:
    """Write the three required files using temporary-file-then-replace."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    nodes_output = assessments.loc[:, NODES_ROLES_COLUMNS].sort_values("gid", kind="mergesort")
    clusters_output = (
        clusters.loc[:, CLUSTERS_COLUMNS].copy().sort_values("cluster_id", kind="mergesort")
    )
    clusters_output["top_gids"] = clusters_output["top_gids"].map(
        lambda gids: (
            json.dumps(list(gids), ensure_ascii=True, separators=(",", ":"))
            if isinstance(gids, (list, tuple))
            else str(gids)
        )
    )
    top_output = top_nodes.loc[:, TOP_NODES_COLUMNS].sort_values("rank", kind="mergesort")
    outputs = {
        "nodes_roles.csv": nodes_output,
        "clusters.csv": clusters_output,
        "top_nodes.csv": top_output,
    }

    paths: dict[str, Path] = {}
    for filename, frame in outputs.items():
        destination = directory / filename
        _atomic_to_csv(frame, destination)
        paths[filename] = destination
    return paths


def _atomic_to_csv(frame: pd.DataFrame, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        frame.to_csv(
            temporary,
            index=False,
            encoding="utf-8",
            lineterminator="\n",
            float_format="%.6f",
        )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
