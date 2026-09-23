from __future__ import annotations

import hashlib

import pytest

import aml_agent.storage.artifacts as artifact_module
from aml_agent.storage import (
    ArtifactStore,
    InvalidArtifactError,
    UnsafeArtifactPathError,
)

RUN_ID = "6ba7b810-9dad-4af6-8640-708e9e8d052f"


def test_csv_write_is_atomic_hashed_and_counts_data_rows(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    artifact = store.write_csv(
        RUN_ID,
        "top_nodes.csv",
        ["rank", "gid", "priority_score"],
        [
            {"rank": 1, "gid": "100000000000000001", "priority_score": 0.91},
            {"rank": 2, "gid": "100000000000000002", "priority_score": 0.87},
        ],
    )

    data = store.read_bytes(RUN_ID, "top_nodes.csv")
    assert artifact.relative_path == f"{RUN_ID}/top_nodes.csv"
    assert artifact.row_count == 2
    assert artifact.size_bytes == len(data)
    assert artifact.sha256 == hashlib.sha256(data).hexdigest()
    assert store.verify(RUN_ID, artifact) is True
    assert store.path_for(RUN_ID, "top_nodes.csv").parent.parent == store.root


def test_failed_replace_leaves_no_partial_artifact_or_temp_file(tmp_path, monkeypatch) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(artifact_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        store.write_json(RUN_ID, {"version": 1})

    artifact_path = store.path_for(RUN_ID, "audit.json")
    assert not artifact_path.exists()
    run_directory = artifact_path.parent
    assert [path for path in run_directory.iterdir() if path.suffix == ".tmp"] == []


def test_artifact_write_is_idempotent_but_content_is_immutable(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    original = store.write_json(RUN_ID, {"version": 1})

    replay = store.write_json(RUN_ID, {"version": 1})
    assert replay == original

    with pytest.raises(InvalidArtifactError, match="different content"):
        store.write_json(RUN_ID, {"version": 2})
    assert store.verify(RUN_ID, original) is True


@pytest.mark.parametrize(
    ("run_id", "name"),
    [
        ("../outside", "audit.json"),
        (RUN_ID, "../audit.json"),
        (RUN_ID, "unapproved.csv"),
        (RUN_ID, "C:\\audit.json"),
    ],
)
def test_path_selectors_cannot_escape_allowlist(tmp_path, run_id, name) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(UnsafeArtifactPathError):
        store.write_text(run_id, name, "unsafe")


def test_csv_bytes_require_utf8_header(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(InvalidArtifactError, match="header"):
        store.write_bytes(RUN_ID, "nodes_roles.csv", b"")
    with pytest.raises(InvalidArtifactError, match="UTF-8"):
        store.write_bytes(RUN_ID, "clusters.csv", b"\xff\xfe")
