import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.api.events import event_stream
from backend.app.api.service import ApiService
from backend.app.main import create_app
from backend.tests.api.conftest import create_and_execute, wait_finished


def parse_sse(response):
    messages = []
    for chunk in response.text.split("\n\n"):
        fields = dict(
            line.split(": ", 1)
            for line in chunk.splitlines()
            if ": " in line and not line.startswith(":")
        )
        if "data" in fields:
            fields["data"] = json.loads(fields["data"])
            messages.append(fields)
    return messages


def test_event_replay_resume_and_terminal_drain(client, backend):
    rid = client.post("/api/runs", json={}).json()["run_id"]
    for i in range(205):
        backend.add_event(rid, "decision", f"Safe fixture decision {i}.")
    backend.finish_fixture(rid)
    response = client.get(f"/api/runs/{rid}/events")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    events = parse_sse(response)
    assert len(events) == 208
    assert [int(event["id"]) for event in events] == list(range(1, 209))
    assert events[-1]["event"] == "completed"
    replay = client.get(f"/api/runs/{rid}/events?after=100", headers={"Last-Event-ID": "206"})
    assert [int(event["id"]) for event in parse_sse(replay)] == [207, 208]
    assert parse_sse(client.get(f"/api/runs/{rid}/events?after=208")) == []
    assert client.app.state.api_service.streams == 0


def test_snapshot_does_not_hang_for_unexecuted_run(client, backend):
    rid = client.post("/api/runs", json={}).json()["run_id"]
    response = client.get(f"/api/runs/{rid}/events?follow=false")
    assert response.status_code == 200 and ": connected" in response.text
    assert parse_sse(response) == []
    for cursor in ("abc", "-1", "1.0", "9223372036854775808"):
        assert (
            client.get(f"/api/runs/{rid}/events", headers={"Last-Event-ID": cursor}).status_code
            == 422
        )


def test_live_stream_observes_events_persisted_after_connection(client, executor):
    executor.gate.clear()
    rid = create_and_execute(client)
    with ThreadPoolExecutor(max_workers=1) as pool:
        stream = pool.submit(client.get, f"/api/runs/{rid}/events")
        deadline = time.monotonic() + 2
        while client.app.state.api_service.streams == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.app.state.api_service.streams == 1
        assert client.post("/api/demo/reset").status_code == 409
        executor.gate.set()
        response = stream.result(timeout=5)
    events = parse_sse(response)
    assert events[0]["event"] == "started"
    assert events[-1]["event"] == "completed"
    assert client.app.state.api_service.streams == 0


def test_execution_idempotency_under_concurrent_requests(client, backend, executor):
    executor.gate.clear()
    rid = client.post("/api/runs", json={}).json()["run_id"]
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(lambda _: client.post(f"/api/runs/{rid}/execute"), range(5)))
    assert all(response.status_code == 202 for response in responses)
    assert sum(response.json()["started"] for response in responses) == 1
    assert backend.claims == executor.calls == 1
    assert client.get("/health").status_code == 200
    assert client.post("/api/demo/reset").status_code == 409
    executor.gate.set()
    wait_finished(client, rid)


def test_capacity_and_shutdown_release_claim(settings, backend, executor):
    settings.api_max_active_runs = 1
    executor.gate.clear()
    with TestClient(create_app(settings, backend=backend, executor=executor)) as client:
        rid = create_and_execute(client)
        other = client.post("/api/runs", json={}).json()["run_id"]
        response = client.post(f"/api/runs/{other}/execute")
        assert response.status_code == 503 and response.headers["retry-after"] == "1"
        assert backend.get_run(other).executing is False
    assert backend.get_run(rid).executing is False
    assert backend.get_run(rid).status == "created"
    assert backend.releases == 1
    # Shutdown preserved state and a later application can resume it.
    executor.gate.set()
    with TestClient(create_app(settings, backend=backend, executor=executor)) as client:
        assert client.post(f"/api/runs/{rid}/execute").status_code == 202
        assert wait_finished(client, rid)["status"] == "completed"


def test_timeout_is_safe_and_releases_claim(settings, backend, executor):
    settings.api_execution_timeout_seconds = 0.03
    executor.gate.clear()
    with TestClient(create_app(settings, backend=backend, executor=executor)) as client:
        rid = create_and_execute(client)
        assert wait_finished(client, rid)["status"] == "failed"
        assert backend.failure_codes == ["EXECUTION_TIMEOUT"]
        assert backend.releases == 1


def test_executor_cannot_return_success_without_terminal_state(settings, backend):
    class PrematureExecutor:
        async def execute_run(self, run_id):
            return None

    with TestClient(create_app(settings, backend=backend, executor=PrematureExecutor())) as client:
        rid = create_and_execute(client)
        assert wait_finished(client, rid)["status"] == "failed"
        assert backend.failure_codes == ["EXECUTION_INCOMPLETE"]


def test_heartbeat_and_disconnect_cleanup(settings, backend, executor):
    class Request:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    async def exercise():
        service = ApiService(settings, backend, executor)
        request = Request()
        rid = str(backend.create_run(dataset_id="bundled", mode="demo", model=None).run_id)
        service.streams = 1
        stream = event_stream(service, request, rid, 0, True)
        assert ": connected" in await anext(stream)
        assert ": heartbeat" in await asyncio.wait_for(anext(stream), timeout=1)
        request.disconnected = True
        assert [message async for message in stream] == []
        assert service.streams == 0
        service.streams = 1
        request.disconnected = False
        stream = event_stream(service, request, rid, 0, True)
        await anext(stream)
        await stream.aclose()
        assert service.streams == 0

    asyncio.run(exercise())


def test_stream_does_not_leak_other_run_or_backend_errors(client, backend):
    rid = client.post("/api/runs", json={}).json()["run_id"]
    backend.add_event(rid, "decision", "private-other-run")
    backend.events[rid][0].run_id = uuid4()
    response = client.get(f"/api/runs/{rid}/events?follow=false")
    assert "private-other-run" not in response.text
    assert parse_sse(response)[0]["data"]["code"] == "EVENT_STREAM_FAILED"
    assert client.app.state.api_service.streams == 0


def test_stream_capacity(settings, backend, executor):
    settings.sse_max_streams = 1
    with TestClient(create_app(settings, backend=backend, executor=executor)) as client:
        rid = client.post("/api/runs", json={}).json()["run_id"]
        client.app.state.api_service.streams = 1
        assert client.get(f"/api/runs/{rid}/events?follow=false").status_code == 503
        client.app.state.api_service.streams = 0
