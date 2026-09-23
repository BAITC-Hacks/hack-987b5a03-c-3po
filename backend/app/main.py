"""ASGI factory and composition root for the Phase 4 HTTP API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.errors import AppError
from .api.phase_adapter import PhaseRunBackend
from .api.phase_executor import PhaseRunExecutor
from .api.ports import RunBackend, RunExecutor
from .api.routes import router
from .api.service import ApiService
from .config import Settings


def create_app(
    settings: Settings | None = None,
    *,
    backend: RunBackend | None = None,
    executor: RunExecutor | None = None,
    integrate: bool = True,
) -> FastAPI:
    settings = settings or Settings()
    if integrate and backend is None and executor is None:
        backend = PhaseRunBackend(
            settings,
            settings.database_path,
            settings.artifacts_dir,
            settings.data_dir,
            settings.uploads_dir,
        )
        executor = PhaseRunExecutor(
            settings,
            settings.database_path,
            settings.artifacts_dir,
            settings.data_dir,
            settings.uploads_dir,
        )

    @asynccontextmanager
    async def lifespan(app):
        app.state.api_service = ApiService(settings, backend, executor)
        try:
            yield
        finally:
            await app.state.api_service.close()

    app = FastAPI(
        title="AML Agent API",
        version="0.1.0",
        lifespan=lifespan,
        description="Run verified AML analysis over the bundled or imported dataset.",
    )

    @app.exception_handler(AppError)
    async def application_error(request, exc):
        headers = {"Retry-After": "1"} if exc.status_code == 503 else None
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}},
            status_code=exc.status_code,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic's input/ctx fields can echo secrets, arbitrary payloads, or paths.
        locations = [list(error["loc"]) for error in exc.errors()]
        return JSONResponse(
            {
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Request validation failed.",
                    "fields": locations,
                }
            },
            status_code=422,
        )

    async def internal_error(request, exc):
        return JSONResponse(
            {"error": {"code": "INTERNAL_ERROR", "message": "The request could not be completed."}},
            status_code=500,
        )

    app.add_exception_handler(ResponseValidationError, internal_error)

    @app.middleware("http")
    async def contain_unhandled_errors(request, call_next):
        # A catch-all exception handler alone re-raises to the ASGI server after
        # sending its response, which can log private exception text. Contain it.
        try:
            return await call_next(request)
        except Exception as exc:
            return await internal_error(request, exc)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
        expose_headers=["Location", "ETag", "Content-Disposition"],
        allow_credentials=False,
    )
    app.include_router(router)
    return app
