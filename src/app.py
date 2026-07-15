from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from src.config import Settings
from src.api.routes import health_router, snapshot_router
from src.api.openapi import contract_v1_openapi
from src.api.routes.dependencies import IncompatibleContractError
from src.factories import create_agent_services
from src.observability import EventCode
from src.security import (
    BearerAuthenticationError,
    bearer_authentication_exception_handler,
    install_logging_redaction,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.factories import AgentServices


def create_app(*, settings: Settings, services: AgentServices | None = None) -> FastAPI:
    install_logging_redaction()
    resolved_services = services or create_agent_services(settings=settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        try:
            await run_in_threadpool(resolved_services.startup_restore)
        except Exception:
            resolved_services.observability.record(EventCode.STARTUP_RESTORE_FAILURE)
            resolved_services.state.record_not_ready()
        yield

    app = FastAPI(
        title="VLESS node agent",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_exception_handler(
        BearerAuthenticationError,
        bearer_authentication_exception_handler,
    )
    app.add_exception_handler(
        IncompatibleContractError,
        incompatible_contract_exception_handler,
    )
    app.state.settings = settings
    app.state.services = resolved_services
    app.state.observability = resolved_services.observability
    app.include_router(health_router)
    app.include_router(snapshot_router)
    app.openapi = contract_v1_openapi  # type: ignore[method-assign]
    return app


async def incompatible_contract_exception_handler(
    request: Request,
    exc: IncompatibleContractError,
) -> JSONResponse:
    del request, exc
    return JSONResponse(
        status_code=426,
        content={
            "code": "incompatible_contract",
            "message": "The requested contract or schema major is not supported.",
        },
    )


def create_app_from_env() -> FastAPI:
    return create_app(settings=Settings())


__all__ = (
    "create_app",
    "create_app_from_env",
    "incompatible_contract_exception_handler",
)
