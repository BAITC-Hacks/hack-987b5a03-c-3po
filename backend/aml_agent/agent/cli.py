"""Run the Phase 3 orchestrator from a local command line."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from aml_agent.storage import ArtifactStore, Database, RunMode
from aml_agent.tool_runtime import ToolRuntime

from .integration import DatabaseEventStore, DatabaseRunRepository, RuntimeToolRegistry
from .models import AgentSettings
from .orchestrator import AgentOrchestrator
from .providers import make_provider


def run_agent(argv: Sequence[str] | None = None) -> dict[str, object]:
    """Execute a new run against a trusted local dataset registration."""

    parser = argparse.ArgumentParser(
        prog="aml-agent-run",
        description="Run the bounded agent workflow over a local parquet batch.",
    )
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--database", type=Path, default=Path("var/aml-agent.sqlite3"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    parser.add_argument("--dataset-id", default="bundled-july")
    parser.add_argument("--expected-seeds", type=int, default=81)
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    args = parser.parse_args(argv)
    if args.mode == "live":
        try:
            from dotenv import load_dotenv
        except ImportError:
            parser.error("Live mode requires the backend[live] extra")
        load_dotenv(override=False)
    settings = AgentSettings.from_env()
    with Database(args.database) as database:
        runtime = ToolRuntime(
            database,
            ArtifactStore(args.artifacts),
            {args.dataset_id: args.data},
            expected_seed_counts={args.dataset_id: args.expected_seeds},
        )
        run = runtime.create_run(
            args.dataset_id,
            RunMode(args.mode),
            model=settings.model if args.mode == "live" else None,
        )
        agent = AgentOrchestrator(
            DatabaseRunRepository(database),
            RuntimeToolRegistry(runtime),
            DatabaseEventStore(database),
            make_provider(args.mode, settings),
            settings=settings,
        )
        return asyncio.run(agent.execute_run(run.run_id)).as_dict()


def agent_main() -> None:
    result = run_agent()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "completed" else 2)


if __name__ == "__main__":
    agent_main()
