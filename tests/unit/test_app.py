from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.app import create_app, create_app_from_env
from src.config import EnvironmentMode, Settings


def test_create_app_exposes_no_routes() -> None:
    settings = Settings(
        vless_node_id="node-01",
        environment_mode=EnvironmentMode.TEST,
    )

    app = create_app(settings=settings)

    assert app.routes == []
    assert app.state.settings is settings


def test_settings_load_typed_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLESS_NODE_ID", "node-01")
    monkeypatch.setenv("ENVIRONMENT_MODE", "local")

    settings = Settings(_env_file=None)

    assert settings.vless_node_id == "node-01"
    assert settings.environment_mode is EnvironmentMode.LOCAL


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
