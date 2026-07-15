from __future__ import annotations

from fastapi import FastAPI

from src.config import Settings
from src.security import (
    BearerAuthenticationError,
    bearer_authentication_exception_handler,
    install_logging_redaction,
)


def create_app(*, settings: Settings) -> FastAPI:
    install_logging_redaction()
    app = FastAPI(
        title="VLESS node agent",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_exception_handler(
        BearerAuthenticationError,
        bearer_authentication_exception_handler,
    )
    app.state.settings = settings
    return app


def create_app_from_env() -> FastAPI:
    return create_app(settings=Settings())


__all__ = ("create_app", "create_app_from_env")
