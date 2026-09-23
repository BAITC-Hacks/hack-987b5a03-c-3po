"""Public strict tool-contract API.

Tool execution and persistence live outside this package layer; this module only
exports provider definitions, state allowlists, and argument validation.
"""

from aml_agent.tools.schemas import (
    ERROR_CODE_ANALYTICS_FAILED,
    ERROR_CODE_CASE_WRITE_FAILED,
    ERROR_CODE_DATASET_INCONSISTENT,
    ERROR_CODE_DATASET_NOT_FOUND,
    ERROR_CODE_DATASET_SCHEMA_INVALID,
    ERROR_CODE_EXPORT_FAILED,
    ERROR_CODE_INTERNAL_ERROR,
    ERROR_CODE_INVALID_ARGUMENT,
    ERROR_CODE_INVALID_STATE,
    ERROR_CODE_VERIFICATION_FAILED,
    STATE_TOOL_ALLOWLIST,
    SUPPORTED_ERROR_CODES,
    TOOL_DEFINITIONS_BY_NAME,
    get_allowed_tool_definitions,
    get_allowed_tool_names,
    get_tool_definition,
    get_tool_definitions,
    validate_tool_arguments,
)

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
    "STATE_TOOL_ALLOWLIST",
    "SUPPORTED_ERROR_CODES",
    "TOOL_DEFINITIONS_BY_NAME",
    "get_allowed_tool_definitions",
    "get_allowed_tool_names",
    "get_tool_definition",
    "get_tool_definitions",
    "validate_tool_arguments",
]
