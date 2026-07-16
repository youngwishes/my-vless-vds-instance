from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from src.observability import EventCode


_bearer_scheme = HTTPBearer(auto_error=False, scheme_name="bearerAuth")


class BearerAuthenticationError(Exception):
    """The request did not provide a valid node bearer credential."""


async def bearer_authentication_exception_handler(
    request: Request,
    exc: BearerAuthenticationError,
) -> JSONResponse:
    del request, exc
    return JSONResponse(
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
        content={
            "code": "unauthorized",
            "message": "Authentication is required.",
        },
    )


async def require_bearer_token(
    *,
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        request.app.state.observability.record(EventCode.AUTH_FAILURE)
        raise BearerAuthenticationError

    settings = request.app.state.settings
    configured_tokens = tuple(
        token.get_secret_value()
        for token in (settings.agent_token_current, settings.agent_token_next)
        if token is not None
    )
    matches = tuple(
        secrets.compare_digest(
            credentials.credentials.encode("utf-8"),
            token.encode("utf-8"),
        )
        for token in configured_tokens
    )
    if not any(matches):
        request.app.state.observability.record(EventCode.AUTH_FAILURE)
        raise BearerAuthenticationError


__all__ = (
    "BearerAuthenticationError",
    "bearer_authentication_exception_handler",
    "require_bearer_token",
)
