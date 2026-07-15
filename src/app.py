from __future__ import annotations

from fastapi import FastAPI

from src.config import Settings


def create_app(*, settings: Settings) -> FastAPI:
    app = FastAPI(
        title="VLESS node agent",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    return app


def create_app_from_env() -> FastAPI:
    return create_app(settings=Settings())


__all__ = ("create_app", "create_app_from_env")
