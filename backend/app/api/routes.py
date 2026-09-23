from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .errors import AppError
from .events import event_stream
from .schemas import (
    ArtifactName,
    CaseView,
    ClusterView,
    CreateRun,
    ExecuteView,
    Gid,
    HealthView,
    NodeDetail,
    NodeView,
    Page,
    ResetView,
    Role,
    RunView,
)
from .service import ApiService

router = APIRouter()


def get_service(request: Request) -> ApiService:
    return request.app.state.api_service


Service = Annotated[ApiService, Depends(get_service)]
Offset = Annotated[int, Query(ge=0, le=2**31 - 1)]
Limit = Annotated[int, Query(ge=1, le=200)]


@router.get("/health", response_model=HealthView, tags=["health"])
def health(service: Service):
    try:
        return service.health()
    except Exception:
        raise AppError("BACKEND_UNAVAILABLE", "Backend health check failed.", 503) from None


@router.post("/api/runs", response_model=RunView, status_code=201, tags=["runs"])
def create_run(body: CreateRun, service: Service, response: Response):
    run = service.create_run(body)
    response.headers["Location"] = f"/api/runs/{run.run_id}"
    return run


@router.get("/api/runs/{run_id}", response_model=RunView, tags=["runs"])
def get_run(run_id: UUID, service: Service):
    return service.require_backend().get_run(str(run_id))


@router.post(
    "/api/runs/{run_id}/execute",
    response_model=ExecuteView,
    status_code=202,
    responses={200: {"model": ExecuteView, "description": "Completed run, no work repeated"}},
    tags=["runs"],
)
async def execute_run(run_id: UUID, service: Service, response: Response):
    result = await service.start(str(run_id))
    if not result.started and not result.executing:
        response.status_code = 200
    response.headers["Location"] = f"/api/runs/{run_id}"
    return result


@router.get(
    "/api/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Persisted execution events"}
    },
    tags=["runs"],
)
async def events(
    run_id: UUID,
    request: Request,
    service: Service,
    after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    follow: bool = True,
    last_event_id: Annotated[str | None, Header(max_length=19)] = None,
):
    if last_event_id is not None:
        if (
            not last_event_id.isascii()
            or not last_event_id.isdigit()
            or int(last_event_id) > 2**63 - 1
        ):
            raise AppError(
                "INVALID_EVENT_CURSOR", "Last-Event-ID must be a non-negative sequence number.", 422
            )
        after = int(last_event_id)
    backend = service.require_backend()
    async with service.lock:
        await run_in_threadpool(backend.get_run, str(run_id))
        if service.streams >= service.settings.sse_max_streams:
            raise AppError("STREAM_CAPACITY", "Too many event streams; retry later.", 503)
        service.streams += 1
    return StreamingResponse(
        event_stream(service, request, str(run_id), after, follow),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/api/runs/{run_id}/nodes", response_model=Page[NodeView], tags=["analysis"])
def nodes(
    run_id: UUID,
    service: Service,
    offset: Offset = 0,
    limit: Limit = 20,
    role: Role | None = None,
    cluster_id: Annotated[int | None, Query(ge=0)] = None,
    gid: Gid | None = None,
    is_seed: bool | None = None,
    truncated_by_depth: bool | None = None,
):
    return service.nodes(
        str(run_id),
        offset=offset,
        limit=limit,
        role=role,
        cluster_id=cluster_id,
        gid=gid,
        is_seed=is_seed,
        truncated_by_depth=truncated_by_depth,
    )


@router.get("/api/runs/{run_id}/nodes/{gid}", response_model=NodeDetail, tags=["analysis"])
def node(
    run_id: UUID,
    gid: Gid,
    service: Service,
    neighbor_hops: Annotated[int, Query(ge=1, le=2)] = 1,
    max_nodes: Annotated[int, Query(ge=1, le=200)] = 100,
    max_edges: Annotated[int, Query(ge=1, le=500)] = 200,
):
    return service.node_detail(
        str(run_id), gid, neighbor_hops=neighbor_hops, max_nodes=max_nodes, max_edges=max_edges
    )


@router.get("/api/runs/{run_id}/clusters", response_model=Page[ClusterView], tags=["analysis"])
def clusters(run_id: UUID, service: Service, offset: Offset = 0, limit: Limit = 20):
    return service.clusters(str(run_id), offset=offset, limit=limit)


@router.get("/api/cases/{case_id}", response_model=CaseView, tags=["cases"])
def case(case_id: UUID, service: Service):
    return service.require_backend().get_case(str(case_id))


@router.get(
    "/api/runs/{run_id}/artifacts/{name}",
    response_class=Response,
    responses={
        200: {
            "content": {
                "text/csv": {},
                "application/json": {},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
            }
        }
    },
    tags=["artifacts"],
)
def artifact(run_id: UUID, name: ArtifactName, service: Service):
    artifact = service.artifact(str(run_id), name)
    if name == "audit.json":
        media_type = "application/json"
    elif name.endswith(".xlsx"):
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        media_type = "text/csv"
    return Response(
        artifact.content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "ETag": f'"{artifact.sha256}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/api/demo/reset", response_model=ResetView, tags=["demo"])
async def reset_demo(service: Service):
    return await service.reset_demo()
