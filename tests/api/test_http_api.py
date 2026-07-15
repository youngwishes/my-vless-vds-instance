from __future__ import annotations

import hashlib
import asyncio
import json
from dataclasses import dataclass
from unittest.mock import Mock

import pytest
from src.api.schemas import SnapshotDTO
from src.app import create_app
from src.config import EnvironmentMode, Settings
from src.factories import AgentServices
from src.services import AgentRuntimeState, GetHealthService, GetSnapshotService, SnapshotCoordinatorService


TOKEN = "explicit-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}", "X-Agent-Contract-Version": "v1"}


@dataclass(frozen=True, slots=True)
class _Response:
    status_code: int
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode()

    def json(self) -> object:
        return json.loads(self.body)


class _Client:
    def __init__(self, app) -> None:
        self.app = app
        self.exceptions: list[Exception] = []

    def get(self, path: str, *, headers: dict[str, str]) -> _Response:
        return self.request("GET", path, headers=headers)

    def put(
        self,
        path: str,
        *,
        headers: dict[str, str],
        json: object | None = None,
        content: bytes | None = None,
    ) -> _Response:
        body = content if content is not None else __import__("json").dumps(json).encode()
        return self.request("PUT", path, headers=headers, body=body)

    def request(
        self, method: str, path: str, *, headers: dict[str, str], body: bytes = b""
    ) -> _Response:
        messages: list[dict[str, object]] = []
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "https", "path": path,
            "raw_path": path.encode(), "query_string": b"",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "client": ("127.0.0.1", 1), "server": ("agent.test", 443), "root_path": "",
        }
        delivered = False

        async def receive() -> dict[str, object]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        try:
            asyncio.run(self.app(scope, receive, send))
        except Exception as error:
            self.exceptions.append(error)
        start = next(message for message in messages if message["type"] == "http.response.start")
        payload = b"".join(
            message.get("body", b"") for message in messages
            if message["type"] == "http.response.body"
        )
        return _Response(status_code=int(start["status"]), body=payload)


def _snapshot(revision: int = 1, *, access_id: int | None = None) -> SnapshotDTO:
    accesses = [] if access_id is None else [{
        "access_id": access_id,
        "uuid": "01890f47-a2d4-7c11-b3e6-89f40d8639f1",
        "access_revision": 1,
    }]
    payload = {"schema_version": "1.0", "snapshot_revision": revision, "accesses": accesses}
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload["snapshot_hash"] = hashlib.sha256(canonical).hexdigest()
    return SnapshotDTO.model_validate(payload)


def _client(*, probe: Mock | None = None, apply: Mock | None = None) -> tuple[_Client, AgentRuntimeState]:
    settings = Settings(
        vless_node_id="node-01",
        environment_mode=EnvironmentMode.TEST,
        agent_token_current=TOKEN,
        agent_sha="a" * 40,
        xray_version="25.7.1",
        xray_image_digest="sha256:" + "b" * 64,
    )
    state = AgentRuntimeState()
    probe = probe or Mock(return_value=True)
    apply = apply or Mock(side_effect=lambda *, snapshot: snapshot)
    services = AgentServices(
        state=state,
        get_health=GetHealthService(
            state=state,
            exact_set_matches=probe,
            agent_sha=settings.agent_sha,
            xray_version=settings.xray_version,
            xray_image_digest=settings.xray_image_digest,
        ),
        get_snapshot=GetSnapshotService(state=state),
        put_snapshot=SnapshotCoordinatorService(
            state=state, apply_snapshot=apply, exact_set_matches=probe
        ),
        startup_restore=Mock(return_value=None),
    )
    return _Client(create_app(settings=settings, services=services)), state


def test_exact_route_surface_only_contains_contract_operations() -> None:
    client, _ = _client()
    operations = {
        (method, route.path)
        for route in client.app.routes
        for method in getattr(route, "methods", set())
    }
    assert operations == {
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/snapshot"),
        ("PUT", "/api/v1/snapshot"),
    }


@pytest.mark.parametrize("path", ("/api/v1/health", "/api/v1/snapshot"))
def test_get_requires_auth_and_exact_contract_header(path: str) -> None:
    client, _ = _client()
    unauthorized = client.get(path, headers={"X-Agent-Contract-Version": "v1"})
    incompatible = client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})

    assert unauthorized.status_code == 401
    assert unauthorized.json() == {"code": "unauthorized", "message": "Authentication is required."}
    assert incompatible.status_code == 426
    assert incompatible.json() == {
        "code": "incompatible_contract",
        "message": "The requested contract or schema major is not supported.",
    }


def test_health_and_snapshot_return_metadata_only() -> None:
    client, state = _client()
    snapshot = _snapshot()
    state.record_applied(snapshot=snapshot, matches=True)

    health = client.get("/api/v1/health", headers=AUTH)
    applied = client.get("/api/v1/snapshot", headers=AUTH)

    assert health.json() == {
        "contract_version": "v1",
        "schema_version": "1.0",
        "agent_sha": "a" * 40,
        "xray_version": "25.7.1",
        "xray_image_digest": "sha256:" + "b" * 64,
        "readiness": "READY",
        "applied_snapshot_revision": 1,
        "applied_snapshot_hash": snapshot.snapshot_hash,
    }
    assert applied.json() == {
        "contract_version": "v1",
        "schema_version": "1.0",
        "snapshot_revision": 1,
        "snapshot_hash": snapshot.snapshot_hash,
    }
    assert "accesses" not in applied.text


def test_put_exact_statuses_and_safe_error_bodies() -> None:
    client, _ = _client()
    first = _snapshot(2)
    assert client.put("/api/v1/snapshot", headers=AUTH, json=first.model_dump(mode="json")).json()["result"] == "applied"
    assert client.put("/api/v1/snapshot", headers=AUTH, json=first.model_dump(mode="json")).json()["result"] == "no_op"

    stale = client.put("/api/v1/snapshot", headers=AUTH, json=_snapshot(1).model_dump(mode="json"))
    conflict_payload = _snapshot(2, access_id=1).model_dump(mode="json")
    conflict = client.put("/api/v1/snapshot", headers=AUTH, json=conflict_payload)

    assert stale.status_code == 409
    assert stale.json() == {"code": "stale_revision", "message": "Snapshot revision is older than the applied revision."}
    assert conflict.status_code == 409
    assert conflict.json() == {"code": "revision_conflict", "message": "Snapshot revision already exists with a different hash."}


def test_raw_body_overflow_is_413_before_apply() -> None:
    apply = Mock()
    client, _ = _client(apply=apply)
    response = client.put(
        "/api/v1/snapshot",
        headers={**AUTH, "Content-Type": "application/json"},
        content=b" " * 1_048_705,
    )
    assert response.status_code == 413
    assert response.json() == {"code": "snapshot_too_large", "message": "Snapshot exceeds the supported contract limits."}
    apply.assert_not_called()


def test_operational_failure_is_generic_unadvertised_500_and_demotes() -> None:
    secret = "01890f47-a2d4-7c11-b3e6-89f40d8639f1 /private/path Bearer token"
    client, state = _client(apply=Mock(side_effect=RuntimeError(secret)))
    response = client.put("/api/v1/snapshot", headers=AUTH, json=_snapshot().model_dump(mode="json"))
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert secret not in response.text
    assert state.read().readiness.value == "NOT_READY"
    assert client.exceptions == []


def test_generated_openapi_has_exact_contract_surface_and_status_matrix() -> None:
    client, _ = _client()
    schema = client.app.openapi()

    assert schema["security"] == [{"bearerAuth": []}]
    assert {
        (method.upper(), path)
        for path, path_item in schema["paths"].items()
        for method in path_item
    } == {
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/snapshot"),
        ("PUT", "/api/v1/snapshot"),
    }
    expected_statuses = {
        ("/api/v1/health", "get"): {"200", "401", "426"},
        ("/api/v1/snapshot", "get"): {"200", "401", "426"},
        ("/api/v1/snapshot", "put"): {"200", "401", "409", "413", "426"},
    }
    for key, statuses in expected_statuses.items():
        operation = schema["paths"][key[0]][key[1]]
        assert set(operation["responses"]) == statuses
        assert operation["parameters"] == [{"$ref": "#/components/parameters/ContractVersion"}]
        assert "422" not in operation["responses"]
    assert schema["components"]["parameters"]["ContractVersion"]["schema"] == {
        "type": "string", "const": "v1"
    }
    assert set(schema["components"]["schemas"]) == {
        "Hash", "Health", "AppliedSnapshot", "ApplyResult", "SafeError"
    }
