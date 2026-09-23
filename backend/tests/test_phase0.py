"""Repository-level reproducibility checks established in Phase 0."""

from hashlib import sha256
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_dataset_files_match_committed_sha256_manifest() -> None:
    data_dir = REPOSITORY_ROOT / "data"
    entries: dict[str, str] = {}
    for line in (data_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, filename = line.split(maxsplit=1)
        assert filename not in entries
        entries[filename] = digest

    assert set(entries) == {"nodes.parquet", "edges.parquet", "transactions.parquet"}
    for filename, expected_digest in entries.items():
        actual_digest = sha256((data_dir / filename).read_bytes()).hexdigest()
        assert actual_digest == expected_digest
