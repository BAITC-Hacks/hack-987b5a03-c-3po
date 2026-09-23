import csv
import hashlib
import io
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.api.ports import ArtifactContent
from backend.app.api.schemas import RunState
from backend.app.main import create_app
from backend.tests.api.conftest import create_and_execute, wait_finished
from backend.tests.api.fakes import GIDS


def test_http_golden_path_contract(client, backend, executor):
    """Only HTTP drives the workflow; the backend is explicitly a test double."""
    assert client.get("/health").json() == {
        "status": "ok",
        "mode": "demo",
        "backend_ready": True,
        "live_configured": False,
    }
    created = client.post("/api/runs", json={"dataset_id": "bundled"})
    assert created.status_code == 201
    run = created.json()
    rid = run["run_id"]
    base = f"/api/runs/{rid}"
    assert created.headers["location"] == base
    assert run["status"] == "created" and run["case_id"] is None
    assert client.get(base + "/nodes").status_code == 409
    assert client.get(base + "/artifacts/top_nodes.csv").status_code == 409
    assert client.post(base + "/execute").status_code == 202
    run = wait_finished(client, rid)
    assert run["status"] == "completed" and run["verification_status"] == "passed"
    page = client.get(base + "/nodes").json()
    assert page["total"] == 25 and len(page["items"]) == 20
    assert [n["gid"] for n in page["items"]] == GIDS[:20]
    assert all(isinstance(n["gid"], str) for n in page["items"])
    second = client.get(base + "/nodes?offset=20&limit=20").json()
    assert [n["gid"] for n in second["items"]] == GIDS[20:]
    detail = client.get(base + "/nodes/" + GIDS[0]).json()
    assert detail["node"]["gid"] == GIDS[0]
    assert detail["ego_graph"]["directed"] is True
    assert all(
        isinstance(e["src"], str) and isinstance(e["dst"], str)
        for e in detail["ego_graph"]["edges"]
    )
    clusters = client.get(base + "/clusters").json()
    assert clusters["total"] == 2
    assert all(isinstance(gid, str) for c in clusters["items"] for gid in c["top_gids"])
    case = client.get("/api/cases/" + run["case_id"]).json()
    assert case["target_gids"] == GIDS[:20]
    assert [target["gid"] for target in case["targets"]] == GIDS[:20]
    for name, count in (("nodes_roles.csv", 25), ("clusters.csv", 2), ("top_nodes.csv", 20)):
        download = client.get(base + "/artifacts/" + name)
        assert download.status_code == 200
        assert download.headers["content-disposition"] == f'attachment; filename="{name}"'
        assert download.headers["etag"] == f'"{hashlib.sha256(download.content).hexdigest()}"'
        assert len(list(csv.DictReader(io.StringIO(download.text)))) == count
    assert client.get(base + "/artifacts/audit.json").json()["passed"] is True
    workbook = client.get(base + "/artifacts/aml_review_report.xlsx")
    assert workbook.status_code == 200
    assert workbook.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert workbook.headers["content-disposition"] == (
        'attachment; filename="aml_review_report.xlsx"'
    )
    repeated = client.post(base + "/execute")
    assert repeated.status_code == 200 and repeated.json()["started"] is False
    assert executor.calls == backend.claims == backend.releases == 1


def test_unwired_app_is_honest_and_startable(settings):
    with TestClient(create_app(settings, integrate=False)) as client:
        assert client.get("/health").json()["backend_ready"] is False
        assert client.get("/docs").status_code == 200
        response = client.post("/api/runs", json={})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "BACKEND_NOT_CONFIGURED"
        assert client.get("/openapi.json").status_code == 200


def test_pagination_filters_and_ego_bounds(client):
    rid = create_and_execute(client)
    wait_finished(client, rid)
    base = f"/api/runs/{rid}"
    assert client.get(base + "/nodes?gid=" + GIDS[1]).json()["total"] == 1
    filtered = client.get(base + "/nodes?cluster_id=1&is_seed=true").json()
    assert [n["gid"] for n in filtered["items"]] == [GIDS[1]]
    assert client.get(base + "/nodes?role=terminal").json()["total"] == 0
    assert client.get(base + "/nodes?offset=1000").json()["items"] == []
    assert client.get(base + "/nodes?truncated_by_depth=true").json()["total"] == 5
    assert client.get(base + "/clusters?offset=1&limit=1").json()["items"][0]["cluster_id"] == 1
    ego_url = base + "/nodes/" + GIDS[0]
    one = client.get(ego_url + "?neighbor_hops=1").json()["ego_graph"]
    assert {n["gid"] for n in one["nodes"]} == {GIDS[i] for i in (0, 1, 3, 5)}
    assert (GIDS[3], GIDS[0]) in {(e["src"], e["dst"]) for e in one["edges"]}
    two = client.get(ego_url + "?neighbor_hops=2").json()["ego_graph"]
    assert {n["gid"] for n in two["nodes"]} == {GIDS[i] for i in (0, 1, 2, 3, 5, 6)}
    assert two["truncated"] is False
    bounded = client.get(ego_url + "?neighbor_hops=2&max_nodes=2&max_edges=1").json()["ego_graph"]
    assert [n["gid"] for n in bounded["nodes"]] == GIDS[:2]
    assert len(bounded["edges"]) == 1 and bounded["truncated"] is True
    edge_limit = client.get(ego_url + "?max_edges=1").json()["ego_graph"]
    assert len(edge_limit["edges"]) == 1 and edge_limit["truncated"] is True
    isolated = client.get(base + "/nodes/" + GIDS[24]).json()["ego_graph"]
    assert len(isolated["nodes"]) == 1 and isolated["edges"] == []


@pytest.mark.parametrize(
    "suffix",
    [
        "/nodes?limit=0",
        "/nodes?limit=201",
        "/nodes?offset=-1",
        "/nodes?role=criminal",
        "/nodes?gid=1e17",
        "/nodes?gid=9007199254741000.0",
        "/nodes?gid=9223372036854775808",
        "/nodes?gid=001",
        "/nodes/-1",
        "/nodes/nope",
        "/clusters?limit=201",
        f"/nodes/{GIDS[0]}?neighbor_hops=3",
        f"/nodes/{GIDS[0]}?neighbor_hops=0",
        f"/nodes/{GIDS[0]}?max_nodes=201",
        f"/nodes/{GIDS[0]}?max_edges=501",
        "/events?after=-1",
        "/artifacts/.env",
        "/artifacts/unknown.csv",
    ],
)
def test_reject_invalid_inputs_before_backend_access(client, suffix):
    response = client.get(f"/api/runs/{uuid4()}" + suffix)
    assert response.status_code == 422


def test_errors_are_scoped_and_sanitized(client, backend):
    rid = str(uuid4())
    for suffix in ("", "/nodes", "/clusters", "/events", "/artifacts/top_nodes.csv"):
        assert client.get(f"/api/runs/{rid}" + suffix).status_code == 404
    assert client.post(f"/api/runs/{rid}/execute").status_code == 404
    assert client.get(f"/api/cases/{uuid4()}").status_code == 404
    assert client.get("/api/runs/not-uuid").status_code == 422
    assert client.post("/api/runs", json={"dataset_id": "../../private"}).status_code == 422
    response = client.post("/api/runs", json={"api_key": "secret-fixture-key"})
    assert response.status_code == 422 and "secret-fixture-key" not in response.text
    assert client.post("/api/runs", content='{"mode": invalid').status_code == 422
    rid = create_and_execute(client)
    wait_finished(client, rid)
    assert client.get(f"/api/runs/{rid}/nodes/1").status_code == 404
    backend.artifacts[rid, "top_nodes.csv"] = ArtifactContent(b"tampered", "0" * 64)
    assert client.get(f"/api/runs/{rid}/artifacts/top_nodes.csv").status_code == 409
    backend.artifacts.pop((rid, "clusters.csv"))
    assert client.get(f"/api/runs/{rid}/artifacts/clusters.csv").status_code == 404


def test_unverified_results_never_download(client, executor):
    executor.verification_passed = False
    rid = create_and_execute(client)
    run = wait_finished(client, rid)
    assert run["status"] == "verification_failed"
    assert client.get(f"/api/runs/{rid}/artifacts/top_nodes.csv").status_code == 409
    assert client.post(f"/api/runs/{rid}/execute").status_code == 409


def test_missing_live_key_no_claim_or_secret_leak(client, backend):
    created = client.post("/api/runs", json={"mode": "live"})
    rid = created.json()["run_id"]
    result = client.post(f"/api/runs/{rid}/execute")
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "OPENAI_KEY_REQUIRED"
    assert backend.claims == 0
    assert client.get(f"/api/runs/{rid}").json()["status"] == "created"


def test_failures_do_not_leak_internal_exception(client, backend, executor):
    executor.fail = True
    rid = create_and_execute(client)
    run = wait_finished(client, rid)
    assert run["status"] == "failed"
    assert backend.failure_codes == ["EXECUTION_FAILED"]
    response = client.get(f"/api/runs/{rid}/events")
    assert "secret-fixture-key" not in response.text and "filesystem path" not in response.text
    assert client.get(f"/api/runs/{rid}/artifacts/top_nodes.csv").status_code == 409


def test_secret_stays_out_of_health_and_server_errors(settings, backend, executor):
    # Construct through validation, as production settings are immutable-by-convention.
    settings = type(settings)(
        _env_file=None, **settings.model_dump(), openai_api_key="secret-fixture-key"
    )
    with TestClient(
        create_app(settings, backend=backend, executor=executor), raise_server_exceptions=True
    ) as client:
        assert "secret-fixture-key" not in client.get("/health").text

        def explode(**kwargs):
            raise RuntimeError("secret-fixture-key private path")

        backend.create_run = explode
        result = client.post("/api/runs", json={})
        assert result.status_code == 500 and "secret-fixture-key" not in result.text


def test_health_failure_returns_503(client, backend):
    def unavailable():
        raise RuntimeError("private-database-path")

    backend.check_health = unavailable
    response = client.get("/health")
    assert response.status_code == 503 and "private-database-path" not in response.text


def test_demo_reset_keeps_live_runs(client, backend):
    demo = create_and_execute(client)
    wait_finished(client, demo)
    live = client.post("/api/runs", json={"mode": "live"}).json()["run_id"]
    assert client.post("/api/demo/reset").json() == {"deleted_runs": 1}
    assert client.get(f"/api/runs/{demo}").status_code == 404
    assert client.get(f"/api/runs/{live}").status_code == 200
    assert client.post("/api/demo/reset").json() == {"deleted_runs": 0}


def test_live_mode_reset_is_disabled(settings, backend, executor):
    settings.demo_mode = False
    with TestClient(create_app(settings, backend=backend, executor=executor)) as client:
        assert client.post("/api/demo/reset").status_code == 403


def test_openapi_and_cors(client):
    schema = client.get("/openapi.json").json()
    assert len(schema["paths"]) == 11
    node_gid = schema["components"]["schemas"]["NodeView"]["properties"]["gid"]
    assert node_gid["type"] == "string"
    assert "additionalProperties" in schema["components"]["schemas"]["CreateRun"]
    cors = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert cors.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.get("/health", headers={"Origin": "https://unknown.example"})
    assert "access-control-allow-origin" not in denied.headers


def test_backend_cannot_expose_completed_without_verification(client, backend):
    rid = client.post("/api/runs", json={}).json()["run_id"]
    with pytest.raises(ValueError, match="verification"):
        backend.update_run(rid, status=RunState.COMPLETED)
