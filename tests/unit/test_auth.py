from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from fastapi import Depends
from pydantic import ValidationError

from src.app import create_app
from src.config import EnvironmentMode, Settings
from src.security.auth import require_bearer_token


CURRENT_TOKEN = "current-node-token-0000000000000001"
NEXT_TOKEN = "next-node-token-000000000000000004"


@dataclass(frozen=True, slots=True)
class _Response:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> object:
        return json.loads(self.body)


def _app(*, current_token: str, next_token: str | None = None):
    app = create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current=current_token,
            agent_token_next=next_token,
        )
    )

    @app.get("/protected", dependencies=[Depends(require_bearer_token)])
    async def protected() -> dict[str, bool]:
        return {"authenticated": True}

    return app


def _get(app, *, authorization: str | None = None) -> _Response:
    messages: list[dict[str, object]] = []
    request_headers = []
    if authorization is not None:
        request_headers.append((b"authorization", authorization.encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/protected",
        "raw_path": b"/protected",
        "query_string": b"",
        "headers": request_headers,
        "client": ("127.0.0.1", 1),
        "server": ("agent.test", 443),
        "root_path": "",
    }
    request_delivered = False

    async def receive() -> dict[str, object]:
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    bodies = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
    return _Response(
        status_code=int(start["status"]),
        headers={
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in start["headers"]
        },
        body=b"".join(bodies),
    )


@pytest.mark.parametrize(
    "authorization",
    (None, "Basic abc", "Bearer", "Bearer wrong-node-token"),
)
def test_missing_malformed_or_wrong_token_returns_contract_safe_401(
    authorization: str | None,
) -> None:
    response = _get(_app(current_token=CURRENT_TOKEN), authorization=authorization)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "code": "unauthorized",
        "message": "Authentication is required.",
    }


@pytest.mark.parametrize("token", (CURRENT_TOKEN, NEXT_TOKEN))
def test_current_and_next_tokens_authenticate_during_rotation(token: str) -> None:
    response = _get(
        _app(current_token=CURRENT_TOKEN, next_token=NEXT_TOKEN),
        authorization=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


def test_token_for_another_node_does_not_authenticate() -> None:
    node_one = _app(current_token=CURRENT_TOKEN)
    node_two = _app(current_token="other-node-token-00000000000000003")

    assert _get(node_one, authorization=f"Bearer {CURRENT_TOKEN}").status_code == 200
    assert _get(node_two, authorization=f"Bearer {CURRENT_TOKEN}").status_code == 401


def test_non_ascii_bearer_token_returns_contract_safe_401() -> None:
    response = _get(
        _app(current_token=CURRENT_TOKEN),
        authorization="Bearer nøt-the-token",
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "code": "unauthorized",
        "message": "Authentication is required.",
    }


def test_authentication_compares_each_configured_token_with_compare_digest() -> None:
    with patch("src.security.auth.secrets.compare_digest", wraps=__import__("secrets").compare_digest) as compare:
        response = _get(
            _app(current_token=CURRENT_TOKEN, next_token=NEXT_TOKEN),
            authorization=f"Bearer {CURRENT_TOKEN}",
        )

    assert response.status_code == 200
    assert compare.call_count == 2


@pytest.mark.parametrize("token", (None, "", " ", "x" * 31))
def test_production_rejects_missing_blank_or_short_current_token(token: str | None) -> None:
    with pytest.raises(ValidationError, match="agent_token_current"):
        Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.PRODUCTION,
            agent_token_current=token,
        )


@pytest.mark.parametrize("mode", (EnvironmentMode.LOCAL, EnvironmentMode.TEST))
def test_local_and_test_modes_require_an_explicit_current_token(
    mode: EnvironmentMode,
) -> None:
    with pytest.raises(ValidationError, match="agent_token_current"):
        Settings(
            vless_node_id="node-01",
            environment_mode=mode,
        )


def test_local_and_test_modes_accept_explicit_current_token() -> None:
    settings = Settings(
        vless_node_id="node-01",
        environment_mode=EnvironmentMode.TEST,
        agent_token_current="explicit-test-token",
    )

    assert settings.agent_token_current is not None
    assert settings.agent_token_current.get_secret_value() == "explicit-test-token"
