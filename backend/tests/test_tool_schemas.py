"""Contract tests for the strict function-tool schema layer."""

from __future__ import annotations

from uuid import uuid4

import pytest
from jsonschema import ValidationError

from aml_agent.safety import SAFE_REVIEW_CASE_TITLES
from aml_agent.tools import (
    STATE_TOOL_ALLOWLIST,
    SUPPORTED_ERROR_CODES,
    TOOL_DEFINITIONS_BY_NAME,
    get_allowed_tool_definitions,
    get_tool_definition,
    get_tool_definitions,
    validate_tool_arguments,
)

RUN_ID = "6ba7b810-9dad-4af6-8640-708e9e8d052f"

EXPECTED_TOOL_NAMES = (
    "inspect_dataset",
    "build_graph",
    "compute_graph_features",
    "cluster_network",
    "assign_roles",
    "rank_targets",
    "get_node_evidence",
    "create_review_case",
    "export_results",
    "verify_run",
)

EXPECTED_STATE_ALLOWLIST = {
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

VALID_ARGUMENTS = {
    "inspect_dataset": {"run_id": RUN_ID},
    "build_graph": {"run_id": RUN_ID},
    "compute_graph_features": {"run_id": RUN_ID, "include_temporal": True},
    "cluster_network": {"run_id": RUN_ID, "resolution": 1.0, "random_seed": 42},
    "assign_roles": {"run_id": RUN_ID, "ruleset_version": "v1"},
    "rank_targets": {"run_id": RUN_ID, "limit": 20},
    "get_node_evidence": {
        "run_id": RUN_ID,
        "gid": "100000002398779100",
        "neighbor_hops": 2,
    },
    "create_review_case": {
        "run_id": RUN_ID,
        "target_gids": [str(100000000000000000 + index) for index in range(20)],
        "title": "Priority structural indicators for analyst review",
    },
    "export_results": {"run_id": RUN_ID, "include_audit": True},
    "verify_run": {"run_id": RUN_ID},
}


def test_canonical_tool_names_and_count() -> None:
    definitions = get_tool_definitions()

    assert len(definitions) == 10
    assert tuple(definition["name"] for definition in definitions) == EXPECTED_TOOL_NAMES
    assert tuple(TOOL_DEFINITIONS_BY_NAME) == EXPECTED_TOOL_NAMES


def test_every_definition_is_a_strict_closed_object_schema() -> None:
    for definition in get_tool_definitions():
        parameters = definition["parameters"]

        assert definition["type"] == "function"
        assert definition["strict"] is True
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])

    title_schema = TOOL_DEFINITIONS_BY_NAME["create_review_case"]["parameters"][
        "properties"
    ]["title"]
    assert title_schema["enum"] == list(SAFE_REVIEW_CASE_TITLES)


def test_definition_helpers_return_deep_copies() -> None:
    one = get_tool_definition("inspect_dataset")
    one["parameters"]["properties"]["run_id"]["type"] = "integer"

    all_definitions = get_tool_definitions()
    all_definitions[0]["name"] = "mutated"

    assert get_tool_definition("inspect_dataset")["parameters"]["properties"]["run_id"][
        "type"
    ] == "string"
    assert get_tool_definitions()[0]["name"] == "inspect_dataset"


def test_state_allowlist_matches_documented_contract() -> None:
    assert dict(STATE_TOOL_ALLOWLIST) == EXPECTED_STATE_ALLOWLIST

    for state, names in EXPECTED_STATE_ALLOWLIST.items():
        definitions = get_allowed_tool_definitions(state)
        assert tuple(definition["name"] for definition in definitions) == names


@pytest.mark.parametrize(("tool_name", "arguments"), VALID_ARGUMENTS.items())
def test_valid_examples_are_accepted(tool_name: str, arguments: dict[str, object]) -> None:
    validated = validate_tool_arguments(tool_name, arguments)

    assert validated == arguments
    assert validated is not arguments


def test_extra_properties_are_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_tool_arguments("inspect_dataset", {"run_id": RUN_ID, "path": "secrets.env"})


@pytest.mark.parametrize(
    "title",
    [
        "Network case 2026",
        "Criminal indicators for review",
        "Terrorist candidates for triage",
        "Виновен: кандидат для проверки",
    ],
)
def test_unsafe_or_non_cautious_case_titles_are_rejected(title: str) -> None:
    arguments = dict(VALID_ARGUMENTS["create_review_case"])
    arguments["title"] = title

    with pytest.raises(ValidationError):
        validate_tool_arguments("create_review_case", arguments)


def test_cautious_russian_case_title_is_accepted() -> None:
    arguments = dict(VALID_ARGUMENTS["create_review_case"])
    arguments["title"] = "Структурные индикаторы для проверки аналитиком"

    assert validate_tool_arguments("create_review_case", arguments) == arguments


@pytest.mark.parametrize("gid", [123, "", "-1", "123.4", "client-123", "１２３"])
def test_invalid_gid_is_rejected(gid: object) -> None:
    with pytest.raises(ValidationError):
        validate_tool_arguments(
            "get_node_evidence",
            {"run_id": RUN_ID, "gid": gid, "neighbor_hops": 1},
        )


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("cluster_network", {"run_id": RUN_ID, "resolution": 0.49, "random_seed": 42}),
        ("cluster_network", {"run_id": RUN_ID, "resolution": 2.01, "random_seed": 42}),
        ("cluster_network", {"run_id": RUN_ID, "resolution": 1.0, "random_seed": -1}),
        ("rank_targets", {"run_id": RUN_ID, "limit": 19}),
        ("rank_targets", {"run_id": RUN_ID, "limit": 101}),
        (
            "get_node_evidence",
            {"run_id": RUN_ID, "gid": "100000002398779100", "neighbor_hops": 3},
        ),
        (
            "create_review_case",
            {"run_id": RUN_ID, "target_gids": [str(index) for index in range(19)], "title": "x"},
        ),
    ],
)
def test_documented_ranges_are_enforced(tool_name: str, arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        validate_tool_arguments(tool_name, arguments)


def test_invalid_uuid_is_rejected_by_format_checker() -> None:
    with pytest.raises(ValidationError):
        validate_tool_arguments("inspect_dataset", {"run_id": "not-a-uuid"})


def test_unknown_tool_and_state_fail_closed() -> None:
    with pytest.raises(KeyError):
        validate_tool_arguments("run_shell", {})
    with pytest.raises(KeyError):
        get_allowed_tool_definitions("unknown")


def test_supported_error_codes_match_contract() -> None:
    assert SUPPORTED_ERROR_CODES == (
        "INVALID_STATE",
        "INVALID_ARGUMENT",
        "DATASET_NOT_FOUND",
        "DATASET_SCHEMA_INVALID",
        "DATASET_INCONSISTENT",
        "ANALYTICS_FAILED",
        "CASE_WRITE_FAILED",
        "EXPORT_FAILED",
        "VERIFICATION_FAILED",
        "INTERNAL_ERROR",
    )


def test_fresh_uuid_remains_valid() -> None:
    arguments = {"run_id": str(uuid4())}

    assert validate_tool_arguments("verify_run", arguments) == arguments
