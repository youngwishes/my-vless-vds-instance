from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentMode(StrEnum):
    LOCAL = "local"
    PRODUCTION = "production"
    TEST = "test"


NodeId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vless_node_id: NodeId
    environment_mode: EnvironmentMode = EnvironmentMode.PRODUCTION


__all__ = ("EnvironmentMode", "Settings")

