"""Versioned deterministic AML role and priority rules."""

from __future__ import annotations

from typing import Final

import numpy as np
import pandas as pd

RULESET_VERSION: Final[str] = "v1"
ROLES: Final[tuple[str, ...]] = (
    "consolidator",
    "transit",
    "distributor",
    "terminal",
    "coordinator",
    "peripheral",
)
ROLE_PRECEDENCE: Final[tuple[str, ...]] = (
    "coordinator",
    "consolidator",
    "distributor",
    "transit",
    "terminal",
    "peripheral",
)


def assign_roles(features: pd.DataFrame, cluster_assignments: pd.DataFrame) -> pd.DataFrame:
    """Assign exactly one role and bounded score to every feature row."""

    working = features.merge(
        cluster_assignments,
        on="gid",
        how="left",
        validate="one_to_one",
    )
    if len(working) != len(features) or working["cluster_id"].isna().any():
        raise ValueError("Every feature row must have exactly one cluster assignment.")
    working["cluster_id"] = working["cluster_id"].astype("int64")

    candidate_scores = _candidate_scores(working)
    role_values: list[str] = []
    score_values: list[float] = []
    evidence_values: list[str] = []

    for index, row in working.iterrows():
        role = _select_role(row)
        if role == "peripheral":
            strongest_other = max(
                float(candidate_scores[candidate].at[index]) for candidate in ROLE_PRECEDENCE[:-1]
            )
            score = max(0.25, 1.0 - strongest_other)
        else:
            score = float(candidate_scores[role].at[index])
        score = round(float(np.clip(score, 0.0, 1.0)), 6)
        evidence = _evidence(role, row)
        if not evidence or len(evidence) > 200 or not any(ch.isdigit() for ch in evidence):
            raise RuntimeError(
                f"Generated evidence violates its contract for GID {int(row['gid'])}."
            )
        role_values.append(role)
        score_values.append(score)
        evidence_values.append(evidence)

    return (
        pd.DataFrame(
            {
                "gid": working["gid"].astype("int64"),
                "role": role_values,
                "role_score": np.asarray(score_values, dtype="float64"),
                "cluster_id": working["cluster_id"].astype("int64"),
                "evidence": evidence_values,
                "ruleset_version": RULESET_VERSION,
            }
        )
        .sort_values("gid", kind="mergesort")
        .reset_index(drop=True)
    )


def score_and_rank(
    features: pd.DataFrame,
    assessments: pd.DataFrame,
    *,
    top_n: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate priority, return full assessments and deterministic top-N."""

    if top_n <= 0:
        raise ValueError("top_n must be positive")
    joined = assessments.merge(
        features.loc[
            :,
            [
                "gid",
                "seed_reach_percentile",
                "pagerank_percentile",
                "betweenness_percentile",
                "max_kzt_percentile",
                "truncated_by_depth",
            ],
        ],
        on="gid",
        how="inner",
        validate="one_to_one",
    )
    if len(joined) != len(features):
        raise ValueError("Priority scoring requires one assessment per feature row.")

    base_priority = (
        0.30 * joined["role_score"]
        + 0.25 * joined["seed_reach_percentile"]
        + 0.20 * joined["pagerank_percentile"]
        + 0.15 * joined["betweenness_percentile"]
        + 0.10 * joined["max_kzt_percentile"]
    )
    boundary_factor = np.where(joined["truncated_by_depth"], 0.90, 1.00)
    joined["priority_score"] = (base_priority * boundary_factor).clip(0.0, 1.0).round(6)

    full = (
        joined.loc[
            :,
            [
                "gid",
                "role",
                "role_score",
                "cluster_id",
                "priority_score",
                "evidence",
                "ruleset_version",
            ],
        ]
        .sort_values("gid", kind="mergesort")
        .reset_index(drop=True)
    )

    ranked = (
        full.sort_values(
            ["priority_score", "role_score", "gid"],
            ascending=[False, False, True],
            kind="mergesort",
        )
        .head(min(top_n, len(full)))
        .reset_index(drop=True)
    )
    top = pd.DataFrame(
        {
            "rank": np.arange(1, len(ranked) + 1, dtype="int64"),
            "gid": ranked["gid"].astype("int64"),
            "role": ranked["role"],
            "priority_score": ranked["priority_score"].astype("float64"),
            "why": ranked["evidence"],
        }
    )
    return full, top


def _candidate_scores(features: pd.DataFrame) -> dict[str, pd.Series]:
    pass_through = features["pass_through_ratio"]
    consolidator_supported = pass_through.notna()
    consolidator = (
        0.45 * _clip(features["in_degree"] / 10.0)
        + 0.35 * _clip((1.0 - pass_through.fillna(1.0)) / 0.8)
        + 0.20 * features["in_kzt_percentile"]
    ).where(consolidator_supported, 0.0)

    distributor = (
        0.50 * features["out_degree_percentile"]
        + 0.30 * _clip(features["out_degree"] / (2.0 * features["in_degree"].clip(lower=1)) / 5.0)
        + 0.20 * features["out_kzt_percentile"]
    )

    transit_supported = pass_through.notna()
    transit = (
        0.50 * _clip(1.0 - (pass_through.fillna(1.2) - 1.0).abs() / 0.2)
        + 0.25 * _clip(np.minimum(features["in_degree"], features["out_degree"]) / 5.0)
        + 0.25 * features["rapid_outflow_ratio"].fillna(0.5)
    ).where(transit_supported, 0.0)

    terminal = 0.45 * features["in_degree_percentile"] + 0.35 * features["in_kzt_percentile"] + 0.20
    return {
        "coordinator": _clip(features["coordinator_index"]),
        "consolidator": _clip(consolidator),
        "distributor": _clip(distributor),
        "transit": _clip(transit),
        "terminal": _clip(terminal),
    }


def _select_role(row: pd.Series) -> str:
    is_seed = bool(row["is_seed"])
    depth = int(row["depth"])
    in_degree = int(row["in_degree"])
    out_degree = int(row["out_degree"])
    ratio = row["pass_through_ratio"]

    if (
        not is_seed
        and depth < 4
        and in_degree > 0
        and out_degree > 0
        and float(row["seed_reach_percentile"]) >= 0.90
        and float(row["coordinator_index_percentile"]) >= 0.99
    ):
        return "coordinator"
    if not is_seed and in_degree >= 5 and pd.notna(ratio) and float(ratio) < 0.5:
        return "consolidator"
    if out_degree >= 10 and out_degree >= 2 * max(in_degree, 1):
        return "distributor"
    if (
        not is_seed
        and depth < 4
        and in_degree >= 2
        and out_degree >= 1
        and pd.notna(ratio)
        and 0.8 <= float(ratio) <= 1.2
    ):
        return "transit"
    if not is_seed and depth < 4 and out_degree == 0 and in_degree >= 2:
        return "terminal"
    return "peripheral"


def _evidence(role: str, row: pd.Series) -> str:
    in_degree = int(row["in_degree"])
    out_degree = int(row["out_degree"])
    in_kzt = _format_amount(float(row["in_kzt"]))
    out_kzt = _format_amount(float(row["out_kzt"]))
    seed_reach = int(row["seed_reach_count"])

    if role == "coordinator":
        bridge = 100.0 * float(row["betweenness_percentile"])
        return (
            f"Observed paths from {seed_reach} seeds; in/out degree "
            f"{in_degree}/{out_degree}; bridge percentile {bridge:.1f}% for review."
        )
    if role == "consolidator":
        pass_pct = 100.0 * float(row["pass_through_ratio"])
        return (
            f"Observed {in_kzt} KZT from {in_degree} payers; sent onward "
            f"{pass_pct:.1f}%; {seed_reach} seeds upstream. Consolidation indicator."
        )
    if role == "distributor":
        return (
            f"Observed {out_kzt} KZT sent to {out_degree} recipients and "
            f"{in_degree} payers; fan-out indicator for review."
        )
    if role == "transit":
        rapid = row["rapid_outflow_ratio"]
        rapid_text = f"{100.0 * float(rapid):.1f}%" if pd.notna(rapid) else "N/A (0 timed)"
        return (
            f"Observed in/out {in_kzt}/{out_kzt} KZT; pass-through "
            f"{float(row['pass_through_ratio']):.2f}; rapid outflow {rapid_text}."
        )
    if role == "terminal":
        return (
            f"Observed {in_kzt} KZT from {in_degree} payers and 0 KZT visible "
            "outflow; recipient indicator for review."
        )
    if bool(row["truncated_by_depth"]):
        return "Depth-4 boundary: 0 visible outgoing edges; role evidence is insufficient."
    if bool(row["is_seed"]) and in_degree == 0 and out_degree == 0:
        return "Seed has 0 visible incoming and 0 outgoing edges; isolated low-evidence candidate."
    return (
        f"Observed in/out degree {in_degree}/{out_degree} and flow "
        f"{in_kzt}/{out_kzt} KZT; no stronger v1 role indicator."
    )


def _format_amount(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 0.005:
        return f"{rounded:,}"
    return f"{value:,.2f}"


def _clip(values: pd.Series) -> pd.Series:
    return values.astype("float64").clip(0.0, 1.0)
