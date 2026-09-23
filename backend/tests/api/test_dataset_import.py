import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from aml_agent.analytics import load_dataset
from backend.app.api.phase_adapter import PhaseRunBackend
from backend.app.api.phase_executor import PhaseRunExecutor
from backend.app.config import Settings
from backend.app.main import create_app

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def test_multiple_csv_files_become_a_registered_four_hop_dataset(tmp_path: Path) -> None:
    backend = PhaseRunBackend(
        Settings(_env_file=None, app_env="test"),
        tmp_path / "aml.db",
        tmp_path / "artifacts",
        DATA_DIR,
        tmp_path / "datasets",
    )
    rows = [
        f"9007199254741000,{9007199254741000 + index},2026-01-{index:02d},{5000 + index}"
        for index in range(1, 21)
    ]
    first = ("january.csv", ("src,dst,date,sum_kzt\n" + "\n".join(rows[:10])).encode())
    second = ("february.csv", ("src,dst,date,sum_kzt\n" + "\n".join(rows[10:])).encode())

    imported = backend.import_dataset(
        files=[first, second], seed_gids=["9007199254741000"]
    )
    dataset = load_dataset(tmp_path / "datasets" / imported.dataset_id)
    run = backend.create_run(dataset_id=imported.dataset_id, mode="demo", model=None)

    assert imported.n_files == 2
    assert imported.n_nodes == 21
    assert imported.n_edges == 20
    assert imported.n_transactions == 20
    assert dataset.nodes["depth"].tolist() == [0] + [1] * 20
    assert run.dataset_id == imported.dataset_id

    executor = PhaseRunExecutor(
        backend.settings,
        tmp_path / "aml.db",
        tmp_path / "artifacts",
        DATA_DIR,
        tmp_path / "datasets",
    )
    asyncio.run(executor.execute_run(str(run.run_id)))
    completed = backend.get_run(str(run.run_id))
    assert completed.status == "completed"
    assert completed.verification_status == "passed"
    assert backend.reset_demo() == 1
    assert not (tmp_path / "datasets" / imported.dataset_id).exists()


def test_http_import_returns_dataset_id_accepted_by_create_run(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        data_dir=DATA_DIR,
        database_url=f"sqlite:///{tmp_path / 'http.db'}",
        artifacts_dir=tmp_path / "artifacts",
        uploads_dir=tmp_path / "datasets",
    )
    rows = "\n".join(
        f"9007199254741000,{9007199254741000 + index},2026-01-{index:02d},{5000 + index}"
        for index in range(1, 21)
    )
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        imported = client.post(
            "/api/datasets/import",
            json={
                "files": [{"name": "transfers.csv", "content": f"src,dst,date,sum_kzt\n{rows}"}],
                "seed_gids": ["9007199254741000"],
            },
        )
        assert imported.status_code == 201, imported.text
        created = client.post(
            "/api/runs",
            json={"dataset_id": imported.json()["dataset_id"], "mode": "demo"},
        )
        assert created.status_code == 201, created.text
