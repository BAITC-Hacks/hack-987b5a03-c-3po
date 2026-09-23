"""The bundled AML workflow, exercised only through the public HTTP surface."""

import csv
import hashlib
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


def _wait_for_completion(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if not run["executing"] and run["status"] in {
            "completed",
            "failed",
            "verification_failed",
        }:
            return run
        time.sleep(0.05)
    pytest.fail("Real bundled run did not finish")


def test_real_bundled_golden_path_over_http(settings):
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/health").json() == {
            "status": "ok",
            "mode": "demo",
            "backend_ready": True,
            "live_configured": False,
        }
        created = client.post("/api/runs", json={"dataset_id": "bundled"})
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]
        base = f"/api/runs/{run_id}"
        assert created.headers["location"] == base
        assert client.get(base + "/nodes").status_code == 409
        assert client.get(base + "/artifacts/top_nodes.csv").status_code == 409
        started = client.post(base + "/execute")
        assert started.status_code == 202, started.text
        assert started.json()["started"] is True

        run = _wait_for_completion(client, run_id)
        assert run["status"] == "completed", run
        assert run["verification_status"] == "passed"
        assert len(run["verification"]) >= 19 and all(run["verification"].values())
        assert run["result"]["status"] == "completed"
        assert run["case_id"]

        event_response = client.get(base + "/events?follow=false")
        assert event_response.status_code == 200
        event_lines = [
            line for line in event_response.text.splitlines() if line.startswith("data: ")
        ]
        events = [json.loads(line[6:]) for line in event_lines]
        assert events[-1]["kind"] == "completed"
        assert any(event["kind"] == "verification" for event in events)
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert "api_key" not in event_response.text.lower()

        first = client.get(base + "/nodes?limit=200")
        assert first.status_code == 200, first.text
        assert first.json()["total"] == 2248
        assert len(first.json()["items"]) == 200
        assert all(isinstance(node["gid"], str) for node in first.json()["items"])
        assert client.get(base + "/nodes?truncated_by_depth=true").json()["total"] == 444
        assert client.get(base + "/clusters").json()["total"] == 91

        top_gid = first.json()["items"][0]["gid"]
        detail = client.get(base + f"/nodes/{top_gid}?neighbor_hops=2&max_nodes=25&max_edges=30")
        assert detail.status_code == 200, detail.text
        graph = detail.json()["ego_graph"]
        assert graph["directed"] is True and graph["center_gid"] == top_gid
        assert len(graph["nodes"]) <= 25 and len(graph["edges"]) <= 30
        assert all(
            isinstance(edge["src"], str) and isinstance(edge["dst"], str)
            for edge in graph["edges"]
        )

        case = client.get("/api/cases/" + run["case_id"])
        assert case.status_code == 200, case.text
        assert len(case.json()["target_gids"]) == 20
        assert [target["gid"] for target in case.json()["targets"]] == case.json()["target_gids"]

        for name, count in (("nodes_roles.csv", 2248), ("clusters.csv", 91), ("top_nodes.csv", 20)):
            response = client.get(base + "/artifacts/" + name)
            assert response.status_code == 200, response.text
            assert response.headers["etag"] == f'"{hashlib.sha256(response.content).hexdigest()}"'
            assert len(list(csv.DictReader(io.StringIO(response.text)))) == count
        audit = client.get(base + "/artifacts/audit.json")
        assert audit.status_code == 200
        assert audit.headers["etag"] == f'"{hashlib.sha256(audit.content).hexdigest()}"'
        assert audit.json()["run_id"] == run_id
        workbook = client.get(base + "/artifacts/aml_review_report.xlsx")
        assert workbook.status_code == 200
        assert workbook.content.startswith(b"PK")
        assert workbook.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert client.post(base + "/execute").status_code == 200

    # Queries must survive a fresh process-local adapter and runtime cache.
    with TestClient(create_app(settings), raise_server_exceptions=False) as restarted:
        assert restarted.get(base).json()["status"] == "completed"
        assert restarted.get(base + f"/nodes/{top_gid}").status_code == 200
        assert restarted.get("/api/cases/" + run["case_id"]).status_code == 200
        live = restarted.post("/api/runs", json={"mode": "live"})
        assert live.status_code == 201
        live_id = live.json()["run_id"]
        missing_key = restarted.post(f"/api/runs/{live_id}/execute")
        assert missing_key.status_code == 409
        assert missing_key.json()["error"]["code"] == "OPENAI_KEY_REQUIRED"
        assert restarted.get(f"/api/runs/{live_id}").json()["status"] == "created"
        assert restarted.post("/api/demo/reset").json()["deleted_runs"] == 1
        assert restarted.get(base).status_code == 404
        assert restarted.get(f"/api/runs/{live_id}").status_code == 200
