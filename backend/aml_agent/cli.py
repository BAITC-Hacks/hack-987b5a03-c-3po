"""Command-line entry points for deterministic AML Agent workflows."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from pathlib import Path

from aml_agent.analytics import analyze_dataset


def _pipeline_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aml-agent-pipeline",
        description="Run deterministic AML graph analytics and write the required CSV files.",
    )
    parser.add_argument("--data", type=Path, default=Path("data"), help="Parquet directory")
    parser.add_argument("--out", type=Path, default=Path("out"), help="Output directory")
    parser.add_argument("--top", type=int, default=20, help="Number of ranked targets")
    parser.add_argument(
        "--expected-seeds",
        type=int,
        default=None,
        help="Optional exact seed-count validation",
    )
    parser.add_argument("--period-start", default=None, help="Declared batch start (YYYY-MM-DD)")
    parser.add_argument("--period-end", default=None, help="Declared batch end (YYYY-MM-DD)")
    return parser


def run_pipeline(argv: Sequence[str] | None = None) -> dict[str, object]:
    """Run Phase 1 from CLI arguments and return a JSON-safe summary."""

    parser = _pipeline_parser()
    args = parser.parse_args(argv)
    if (args.period_start is None) != (args.period_end is None):
        parser.error("--period-start and --period-end must be provided together")
    expected_period = (
        None
        if args.period_start is None
        else (str(args.period_start), str(args.period_end))
    )
    started = time.perf_counter()
    result = analyze_dataset(
        args.data,
        output_dir=args.out,
        top_n=args.top,
        expected_seed_count=args.expected_seeds,
        expected_period=expected_period,
    )
    elapsed = time.perf_counter() - started
    return {
        "status": "completed",
        "dataset_sha256": result.dataset_sha256,
        "ruleset_version": result.ruleset_version,
        "nodes": int(len(result.assessments)),
        "clusters": int(len(result.clusters)),
        "ranked_targets": int(len(result.top_nodes)),
        "artifacts": {name: str(path) for name, path in result.artifact_paths.items()},
        "elapsed_seconds": round(elapsed, 3),
    }


def pipeline_main() -> None:
    """Installed `aml-agent-pipeline` console entry point."""

    print(json.dumps(run_pipeline(), ensure_ascii=False, indent=2))


def tools_main() -> None:
    """Installed Phase 2 entry point; implementation is added with the tool runtime."""

    from aml_agent.tool_runtime import tools_cli_main

    tools_cli_main()


if __name__ == "__main__":
    pipeline_main()
