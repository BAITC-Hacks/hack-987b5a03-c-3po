"""Canonical state allowlist and application-side tool-call validation."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from aml_agent.tools import TOOL_DEFINITIONS_BY_NAME, get_allowed_tool_definitions

from .models import RunContext, RunState, ToolCall, ToolResult, is_valid_gid

TOOL_NAMES_BY_STATE: dict[RunState, tuple[str, ...]] = {
    RunState.CREATED: ("inspect_dataset",),
    RunState.VALIDATED: ("build_graph",),
    RunState.GRAPH_READY: ("compute_graph_features",),
    RunState.ANALYZED: ("cluster_network",),
    RunState.CLUSTERED: ("assign_roles",),
    RunState.CLASSIFIED: ("rank_targets", "get_node_evidence"),
    RunState.RANKED: ("get_node_evidence", "create_review_case"),
    RunState.CASE_CREATED: ("get_node_evidence", "export_results"),
    RunState.EXPORTED: ("verify_run",),
    RunState.VERIFIED: ("get_node_evidence",),
    RunState.COMPLETED: ("get_node_evidence",),
}

NEXT_WRITE_TOOL: dict[RunState, str] = {
    RunState.CREATED: "inspect_dataset",
    RunState.VALIDATED: "build_graph",
    RunState.GRAPH_READY: "compute_graph_features",
    RunState.ANALYZED: "cluster_network",
    RunState.CLUSTERED: "assign_roles",
    RunState.CLASSIFIED: "rank_targets",
    RunState.RANKED: "create_review_case",
    RunState.CASE_CREATED: "export_results",
    RunState.EXPORTED: "verify_run",
}

NEXT_STATE_BY_TOOL: dict[str, RunState] = {
    "inspect_dataset": RunState.VALIDATED,
    "build_graph": RunState.GRAPH_READY,
    "compute_graph_features": RunState.ANALYZED,
    "cluster_network": RunState.CLUSTERED,
    "assign_roles": RunState.CLASSIFIED,
    "rank_targets": RunState.RANKED,
    "create_review_case": RunState.CASE_CREATED,
    "export_results": RunState.EXPORTED,
    "verify_run": RunState.VERIFIED,
}

READ_ONLY_TOOLS = frozenset({"get_node_evidence"})

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = dict(TOOL_DEFINITIONS_BY_NAME)


class InvalidToolCall(ValueError):
    """An untrusted provider call violated the current contract."""


def allowed_definitions(state: RunState) -> list[dict[str, Any]]:
    return get_allowed_tool_definitions(state)


def require_allowed_definitions(state: RunState, definitions: list[dict[str, Any]]) -> None:
    """Do not let an accidental registry change expose another model tool."""
    allowed = set(TOOL_NAMES_BY_STATE.get(state, ()))
    seen: set[str] = set()
    for definition in definitions:
        if not isinstance(definition, dict):
            raise ValueError("Invalid registry definition")
        name = definition.get("name")
        if name not in allowed or name in seen or definition != TOOL_DEFINITIONS[name]:
            raise ValueError("Registry definition does not match the state contract")
        seen.add(name)
    if state in NEXT_WRITE_TOOL and NEXT_WRITE_TOOL[state] not in seen:
        raise ValueError("Required state transition is unavailable")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise InvalidToolCall("Duplicate JSON field")
        output[key] = value
    return output


def _reject_constant(value: str) -> None:
    raise InvalidToolCall("Non-finite JSON number")


def _validate_schema(value: Any, schema: Mapping[str, Any]) -> None:
    kind = schema["type"]
    if isinstance(kind, list):
        if value is None and "null" in kind:
            return
        kind = next(item for item in kind if item != "null")
    if kind == "object":
        if not isinstance(value, dict):
            raise InvalidToolCall("Expected JSON object")
        properties = schema["properties"]
        if set(value) != set(schema["required"]) or set(value) - set(properties):
            raise InvalidToolCall("Missing or unexpected tool argument")
        for key, item in value.items():
            _validate_schema(item, properties[key])
    elif kind == "array":
        if not isinstance(value, list):
            raise InvalidToolCall("Expected JSON array")
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 1000):
            raise InvalidToolCall("Tool array length is out of bounds")
        for item in value:
            _validate_schema(item, schema["items"])
    elif kind == "string":
        if not isinstance(value, str):
            raise InvalidToolCall("Expected string argument")
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 10000):
            raise InvalidToolCall("String length is out of bounds")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise InvalidToolCall("String does not match the required pattern")
        if schema.get("pattern") == "^[0-9]+$" and (len(value) > 19 or int(value) > 2**63 - 1):
            raise InvalidToolCall("GID is outside signed int64")
        if schema.get("format") == "uuid":
            try:
                UUID(value)
            except ValueError as exc:
                raise InvalidToolCall("Invalid UUID argument") from exc
    elif kind == "integer":
        if type(value) is not int:
            raise InvalidToolCall("Expected integer argument")
    elif kind == "number":
        if type(value) not in (int, float):
            raise InvalidToolCall("Expected numeric argument")
    elif kind == "boolean":
        if type(value) is not bool:
            raise InvalidToolCall("Expected boolean argument")
    else:
        raise ValueError("Unsupported canonical tool schema")
    if "enum" in schema and value not in schema["enum"]:
        raise InvalidToolCall("Argument is outside the allowed values")
    if kind in ("integer", "number"):
        if kind == "number" and not math.isfinite(value):
            raise InvalidToolCall("Numeric argument must be finite")
        if value < schema.get("minimum", float("-inf")) or value > schema.get(
            "maximum", float("inf")
        ):
            raise InvalidToolCall("Numeric argument is out of bounds")


def validate_call(
    call: ToolCall, context: RunContext, exposed: list[dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(call.call_id, str) or not call.call_id or len(call.call_id) > 200:
        raise InvalidToolCall("Invalid call ID")
    names = {item["name"] for item in exposed}
    if call.name not in names:
        raise InvalidToolCall("Tool is unavailable in the current state")
    if isinstance(call.arguments, str):
        try:
            arguments = json.loads(
                call.arguments,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise InvalidToolCall("Arguments are not valid JSON") from exc
    else:
        arguments = dict(call.arguments) if isinstance(call.arguments, Mapping) else call.arguments
    _validate_schema(arguments, TOOL_DEFINITIONS[call.name]["parameters"])
    if arguments["run_id"] != context.run_id:
        raise InvalidToolCall("Tool call targets a different run")
    if call.name == "create_review_case":
        target_gids = arguments["target_gids"]
        if len(set(target_gids)) != len(target_gids) or tuple(target_gids) != context.target_gids:
            raise InvalidToolCall("Case targets must match the ranked snapshot")
    return arguments


_SAFE_DATA_FIELDS: dict[str, tuple[str, ...]] = {
    "inspect_dataset": ("dataset_sha256", "n_nodes", "n_edges", "n_transactions", "n_seed"),
    "build_graph": ("n_nodes", "n_edges", "n_orphan_nodes", "n_truncated_depth4"),
    "compute_graph_features": ("feature_version", "temporal_enabled"),
    "cluster_network": ("algorithm", "resolution", "random_seed", "n_clusters"),
    "assign_roles": ("ruleset_version", "assigned_count", "uncertain_count"),
    "rank_targets": ("limit", "ranked_count", "target_gids"),
    "get_node_evidence": ("node", "cluster", "uncertainty_flags"),
    "create_review_case": ("case_id", "status", "target_count", "idempotent_replay"),
    "export_results": ("artifact_names",),
    "verify_run": ("passed", "failed_checks"),
}


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:240]
    if isinstance(value, (list, tuple)) and depth == 0:
        return [_safe_value(item, depth=1) for item in value[:100]]
    return None


def _safe_node(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    allowed = (
        "gid",
        "role",
        "role_score",
        "priority_score",
        "evidence",
        "cluster_id",
        "depth",
        "is_seed",
        "in_degree",
        "out_degree",
        "in_kzt",
        "out_kzt",
        "truncated_by_depth",
    )
    output = {key: _safe_value(value[key]) for key in allowed if key in value and key != "gid"}
    if is_valid_gid(value.get("gid")):
        output["gid"] = value["gid"]
    return output


def _safe_cluster(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    allowed = ("cluster_id", "n_nodes", "n_seed", "hypothesis")
    return {key: _safe_value(value[key]) for key in allowed if key in value}


def safe_tool_output(result: ToolResult) -> dict[str, Any]:
    """Bound the model-visible result; never forward arbitrary tool text or rows."""
    data = {
        key: _safe_value(result.data[key])
        for key in _SAFE_DATA_FIELDS.get(result.tool, ())
        if key in result.data
    }
    if result.tool == "get_node_evidence":
        data["node"] = _safe_node(result.data.get("node"))
        data["cluster"] = _safe_cluster(result.data.get("cluster"))
    if result.tool == "rank_targets" and "target_gids" in data:
        data["target_gids"] = [
            gid
            for gid in (data["target_gids"] if isinstance(data["target_gids"], list) else [])
            if is_valid_gid(gid)
        ]
    output: dict[str, Any] = {
        "ok": result.ok,
        "tool": result.tool,
        "run_id": result.run_id,
        "data": data,
    }
    if result.error is not None:
        output["error"] = {"code": result.error.code, "retriable": result.error.retriable}
    return output
