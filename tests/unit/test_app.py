from __future__ import annotations

from pathlib import Path
import asyncio
import threading
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from src.app import create_app, create_app_from_env
from src.config import EnvironmentMode, Settings
from src.factories import AgentServices
from src.observability import Observability
from src.services import AgentRuntimeState


def test_create_app_exposes_only_exact_snapshot_contract_routes() -> None:
    settings = Settings(
        vless_node_id="node-01",
        environment_mode=EnvironmentMode.TEST,
        agent_token_current="explicit-test-token",
    )

    app = create_app(settings=settings)

    assert {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
    } == {
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/snapshot"),
        ("PUT", "/api/v1/snapshot"),
    }
    assert app.state.settings is settings


def test_settings_load_typed_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLESS_NODE_ID", "node-01")
    monkeypatch.setenv("ENVIRONMENT_MODE", "local")
    monkeypatch.setenv("AGENT_TOKEN_CURRENT", "explicit-local-token")

    settings = Settings(_env_file=None)

    assert settings.vless_node_id == "node-01"
    assert settings.environment_mode is EnvironmentMode.LOCAL
    assert settings.agent_token_current.get_secret_value() == "explicit-local-token"


def test_startup_restore_is_offloaded_from_async_event_loop() -> None:
    settings = Settings(
        vless_node_id="node-01",
        environment_mode=EnvironmentMode.TEST,
        agent_token_current="explicit-test-token",
    )
    called_from: list[int] = []
    startup = Mock(side_effect=lambda: called_from.append(threading.get_ident()))
    observer = Observability()
    services = AgentServices(
        state=AgentRuntimeState(observer=observer),
        get_health=Mock(),
        get_snapshot=Mock(),
        put_snapshot=Mock(),
        startup_restore=startup,
        observer=observer,
    )
    app = create_app(settings=settings, services=services)

    async def run_lifespan() -> int:
        event_loop_thread = threading.get_ident()
        async with app.router.lifespan_context(app):
            pass
        return event_loop_thread

    event_loop_thread = asyncio.run(run_lifespan())

    assert called_from
    assert called_from[0] != event_loop_thread


def test_production_image_contains_canonical_openapi_runtime_artifact() -> None:
    dockerfile = (Path(__file__).parents[2] / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY docs/contracts/v1 ./docs/contracts/v1" in dockerfile


def test_environment_app_factory_requires_node_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VLESS_NODE_ID", raising=False)
    monkeypatch.setenv("ENVIRONMENT_MODE", "production")

    with pytest.raises(ValidationError, match="vless_node_id"):
        create_app_from_env()


def test_environment_app_factory_rejects_whitespace_node_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VLESS_NODE_ID", "   ")
    monkeypatch.setenv("ENVIRONMENT_MODE", "production")

    with pytest.raises(ValidationError, match="vless_node_id"):
        create_app_from_env()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("AGENT_SHA", "0" * 40),
        ("XRAY_VERSION", "unknown"),
        ("XRAY_VERSION", "   "),
        ("XRAY_VERSION", "replace-with-pinned-xray-version"),
        ("XRAY_VERSION", "local"),
        ("XRAY_IMAGE_DIGEST", "sha256:" + "0" * 64),
        ("XRAY_API_TARGET", "   "),
        ("XRAY_MANAGED_INBOUND_TAG", "   "),
        ("XRAY_API_TIMEOUT_SECONDS", "0"),
    ),
)
def test_production_environment_factory_rejects_false_or_unsafe_runtime_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VLESS_NODE_ID", "node-01")
    monkeypatch.setenv("ENVIRONMENT_MODE", "production")
    monkeypatch.setenv("AGENT_TOKEN_CURRENT", "x" * 32)
    monkeypatch.setenv("AGENT_SHA", "a" * 40)
    monkeypatch.setenv("XRAY_VERSION", "25.7.1")
    monkeypatch.setenv("XRAY_IMAGE_DIGEST", "sha256:" + "b" * 64)
    monkeypatch.setenv("XRAY_API_TARGET", "127.0.0.1:10085")
    monkeypatch.setenv("XRAY_MANAGED_INBOUND_TAG", "vless-managed")
    monkeypatch.setenv("XRAY_API_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv(field, value)

    with pytest.raises(ValidationError):
        create_app_from_env()


def test_production_environment_factory_accepts_pinned_numeric_xray_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VLESS_NODE_ID", "node-01")
    monkeypatch.setenv("ENVIRONMENT_MODE", "production")
    monkeypatch.setenv("AGENT_TOKEN_CURRENT", "x" * 32)
    monkeypatch.setenv("AGENT_SHA", "a" * 40)
    monkeypatch.setenv("XRAY_VERSION", "26.7.11")
    monkeypatch.setenv("XRAY_IMAGE_DIGEST", "sha256:" + "b" * 64)

    app = create_app_from_env()

    assert app.state.settings.xray_version == "26.7.11"
