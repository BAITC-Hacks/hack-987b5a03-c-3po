"""Strict OpenAI function-tool contracts for AML Agent.

The definitions in this module mirror ``docs/TOOLS.md``.  Callers receive deep
copies so provider-specific code cannot mutate the canonical application
contracts accidentally.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import Any, Final

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from aml_agent.safety import SAFE_REVIEW_CASE_TITLES, is_safe_review_case_title

JsonObject = dict[str, Any]


ERROR_CODE_INVALID_STATE: Final = "INVALID_STATE"
ERROR_CODE_INVALID_ARGUMENT: Final = "INVALID_ARGUMENT"
ERROR_CODE_DATASET_NOT_FOUND: Final = "DATASET_NOT_FOUND"
ERROR_CODE_DATASET_SCHEMA_INVALID: Final = "DATASET_SCHEMA_INVALID"
ERROR_CODE_DATASET_INCONSISTENT: Final = "DATASET_INCONSISTENT"
ERROR_CODE_ANALYTICS_FAILED: Final = "ANALYTICS_FAILED"
ERROR_CODE_CASE_WRITE_FAILED: Final = "CASE_WRITE_FAILED"
ERROR_CODE_EXPORT_FAILED: Final = "EXPORT_FAILED"
ERROR_CODE_VERIFICATION_FAILED: Final = "VERIFICATION_FAILED"
ERROR_CODE_INTERNAL_ERROR: Final = "INTERNAL_ERROR"

SUPPORTED_ERROR_CODES: Final[tuple[str, ...]] = (
    ERROR_CODE_INVALID_STATE,
    ERROR_CODE_INVALID_ARGUMENT,
    ERROR_CODE_DATASET_NOT_FOUND,
    ERROR_CODE_DATASET_SCHEMA_INVALID,
    ERROR_CODE_DATASET_INCONSISTENT,
    ERROR_CODE_ANALYTICS_FAILED,
    ERROR_CODE_CASE_WRITE_FAILED,
    ERROR_CODE_EXPORT_FAILED,
    ERROR_CODE_VERIFICATION_FAILED,
    ERROR_CODE_INTERNAL_ERROR,
)


_TOOL_DEFINITIONS: tuple[JsonObject, ...] = (
    {
        "type": "function",
        "name": "inspect_dataset",
        "description": (
            "Validate the run's three parquet files, reconcile transactions with "
            "aggregated edges, fingerprint the dataset, and record data limitations. "
            "Does not build or score the graph."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Existing analysis run in created state.",
                }
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "build_graph",
        "description": (
            "Build and persist the directed weighted graph for a validated run, "
            "including isolated input nodes and boundary flags."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Run in validated state.",
                }
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "compute_graph_features",
        "description": (
            "Compute deterministic structural features and, when requested and available, "
            "supporting temporal signals. Does not assign roles."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Run in graph_ready state.",
                },
                "include_temporal": {
                    "type": "boolean",
                    "description": (
                        "Whether to calculate date-based rapid-outflow signals from "
                        "transactions.parquet."
                    ),
                },
            },
            "required": ["run_id", "include_temporal"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "cluster_network",
        "description": (
            "Assign every node to a reproducible Louvain community using an undirected "
            "projection only for community detection, while preserving directed features "
            "for roles."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Run with computed graph features.",
                },
                "resolution": {
                    "type": "number",
                    "minimum": 0.5,
                    "maximum": 2.0,
                    "description": "Louvain resolution; MVP uses 1.0.",
                },
                "random_seed": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 2147483647,
                    "description": "Deterministic random seed; MVP uses 42.",
                },
            },
            "required": ["run_id", "resolution", "random_seed"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "assign_roles",
        "description": (
            "Apply the documented deterministic ruleset to every node, producing one role, "
            "rule-strength score, numeric evidence, and uncertainty flags."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Run with features and cluster assignments.",
                },
                "ruleset_version": {
                    "type": "string",
                    "enum": ["v1"],
                    "description": "Immutable role and scoring ruleset version.",
                },
            },
            "required": ["run_id", "ruleset_version"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "rank_targets",
        "description": (
            "Calculate priority scores from persisted evidence and produce an ordered review "
            "list. The result is a triage priority, not a guilt probability."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Run with complete node assessments.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 20,
                    "maximum": 100,
                    "description": "Number of ranked targets; golden path uses 20.",
                },
            },
            "required": ["run_id", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_node_evidence",
        "description": (
            "Return calculated evidence, uncertainty, cluster context, and a bounded directed "
            "ego graph for one GID. This tool is read-only."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Analyzed run identifier.",
                },
                "gid": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                    "description": "Client GID encoded as a decimal string.",
                },
                "neighbor_hops": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2,
                    "description": "Maximum ego-graph radius.",
                },
            },
            "required": ["run_id", "gid", "neighbor_hops"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_review_case",
        "description": (
            "Create one local analyst review case from the complete ranked target "
            "snapshot. This does not block accounts or contact an external system."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Run in ranked state.",
                },
                "target_gids": {
                    "type": "array",
                    "items": {"type": "string", "pattern": "^[0-9]+$"},
                    "minItems": 20,
                    "maxItems": 100,
                    "description": (
                        "Ordered target snapshot; each GID must exist in the run's ranking."
                    ),
                },
                "title": {
                    "type": "string",
                    "enum": list(SAFE_REVIEW_CASE_TITLES),
                    "description": "Server-approved cautious analyst-facing case title.",
                },
            },
            "required": ["run_id", "target_gids", "title"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "export_results",
        "description": (
            "Write the three required CSV files, a formatted Excel review report, and an "
            "optional JSON audit bundle to the run's controlled artifact directory."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Run with a persisted review case.",
                },
                "include_audit": {
                    "type": "boolean",
                    "description": (
                        "Whether to include audit.json alongside the mandatory CSV files and "
                        "Excel review report."
                    ),
                },
            },
            "required": ["run_id", "include_audit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "verify_run",
        "description": (
            "Independently recompute authoritative analytics and verify database invariants, "
            "CSV schemas and counts, roles, scores, evidence, clusters, ranking order, "
            "artifact hashes, and review-case consistency."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Run with exported artifacts.",
                }
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
)


TOOL_DEFINITIONS_BY_NAME: Final[Mapping[str, JsonObject]] = MappingProxyType(
    {definition["name"]: definition for definition in _TOOL_DEFINITIONS}
)

STATE_TOOL_ALLOWLIST: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "created": ("inspect_dataset",),
        "validated": ("build_graph",),
        "graph_ready": ("compute_graph_features",),
        "analyzed": ("cluster_network",),
        "clustered": ("assign_roles",),
        "classified": ("rank_targets", "get_node_evidence"),
        "ranked": ("get_node_evidence", "create_review_case"),
        "case_created": ("get_node_evidence", "export_results"),
        "exported": ("verify_run",),
        "verified": ("get_node_evidence",),
        "completed": ("get_node_evidence",),
        "verification_failed": (),
        "failed": (),
    }
)

_FORMAT_CHECKER = FormatChecker()
_ARGUMENT_VALIDATORS: Final[Mapping[str, Draft202012Validator]] = MappingProxyType(
    {
        name: Draft202012Validator(definition["parameters"], format_checker=_FORMAT_CHECKER)
        for name, definition in TOOL_DEFINITIONS_BY_NAME.items()
    }
)

for _definition in _TOOL_DEFINITIONS:
    Draft202012Validator.check_schema(_definition["parameters"])


def _state_value(state: str | Any) -> str:
    """Return a string state while allowing future string-valued enums."""

    value = getattr(state, "value", state)
    if not isinstance(value, str):
        raise KeyError(f"Unknown run state: {value!r}")
    return value


def get_tool_definitions() -> list[JsonObject]:
    """Return all canonical tool definitions in workflow order."""

    return deepcopy(list(_TOOL_DEFINITIONS))


def get_tool_definition(tool_name: str) -> JsonObject:
    """Return an independent copy of one tool definition."""

    try:
        definition = TOOL_DEFINITIONS_BY_NAME[tool_name]
    except KeyError as exc:
        raise KeyError(f"Unknown tool: {tool_name!r}") from exc
    return deepcopy(definition)


def get_allowed_tool_names(state: str | Any) -> tuple[str, ...]:
    """Return tool names exposed in a persisted run state."""

    state_value = _state_value(state)
    try:
        return STATE_TOOL_ALLOWLIST[state_value]
    except KeyError as exc:
        raise KeyError(f"Unknown run state: {state_value!r}") from exc


def get_allowed_tool_definitions(state: str | Any) -> list[JsonObject]:
    """Return independent definitions for only the tools allowed in ``state``."""

    return [get_tool_definition(name) for name in get_allowed_tool_names(state)]


def validate_tool_arguments(tool_name: str, arguments: Any) -> JsonObject:
    """Validate proposed arguments and return a defensive copy.

    ``jsonschema.ValidationError`` is raised for malformed arguments. Unknown
    names raise ``KeyError`` and cannot be used to select an arbitrary schema.
    UUID formats are checked explicitly through ``FormatChecker``.
    """

    try:
        validator = _ARGUMENT_VALIDATORS[tool_name]
    except KeyError as exc:
        raise KeyError(f"Unknown tool: {tool_name!r}") from exc

    candidate = deepcopy(arguments)
    validator.validate(candidate)
    if tool_name == "create_review_case" and len(set(candidate["target_gids"])) != len(
        candidate["target_gids"]
    ):
        raise ValidationError("target_gids must be unique")
    if tool_name == "create_review_case" and not is_safe_review_case_title(
        candidate["title"]
    ):
        raise ValidationError(
            "review case title must be one of the fixed reviewed MVP titles"
        )
    return candidate


__all__ = [
    "ERROR_CODE_ANALYTICS_FAILED",
    "ERROR_CODE_CASE_WRITE_FAILED",
    "ERROR_CODE_DATASET_INCONSISTENT",
    "ERROR_CODE_DATASET_NOT_FOUND",
    "ERROR_CODE_DATASET_SCHEMA_INVALID",
    "ERROR_CODE_EXPORT_FAILED",
    "ERROR_CODE_INTERNAL_ERROR",
    "ERROR_CODE_INVALID_ARGUMENT",
    "ERROR_CODE_INVALID_STATE",
    "ERROR_CODE_VERIFICATION_FAILED",
    "SAFE_REVIEW_CASE_TITLES",
    "STATE_TOOL_ALLOWLIST",
    "SUPPORTED_ERROR_CODES",
    "TOOL_DEFINITIONS_BY_NAME",
    "get_allowed_tool_definitions",
    "get_allowed_tool_names",
    "get_tool_definition",
    "get_tool_definitions",
    "validate_tool_arguments",
]
