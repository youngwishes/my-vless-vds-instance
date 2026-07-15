from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, StringConstraints, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentMode(StrEnum):
    LOCAL = "local"
    PRODUCTION = "production"
    TEST = "test"


NodeId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
MINIMUM_PRODUCTION_TOKEN_LENGTH = 32
_BEARER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9\-._~+/]+={0,}")
_AGENT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_IMAGE_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_XRAY_VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vless_node_id: NodeId
    environment_mode: EnvironmentMode = EnvironmentMode.PRODUCTION
    agent_token_current: SecretStr
    agent_token_next: SecretStr | None = None
    agent_sha: str = "0" * 40
    xray_version: str = "unknown"
    xray_image_digest: str = "sha256:" + "0" * 64
    xray_api_target: str = "127.0.0.1:10085"
    xray_api_timeout_seconds: float = Field(default=5.0, gt=0)
    xray_managed_inbound_tag: str = "vless-managed"
    snapshot_path: Path = Path("/var/lib/vless-agent/snapshot.json")

    @field_validator("agent_sha")
    @classmethod
    def _validate_agent_sha(cls, value: str) -> str:
        if _AGENT_SHA_PATTERN.fullmatch(value) is None:
            raise ValueError("agent_sha must be an exact lowercase commit SHA")
        return value

    @field_validator("xray_image_digest")
    @classmethod
    def _validate_image_digest(cls, value: str) -> str:
        if _IMAGE_DIGEST_PATTERN.fullmatch(value) is None:
            raise ValueError("xray_image_digest must be an immutable sha256 digest")
        return value

    @field_validator("xray_api_target", "xray_managed_inbound_tag")
    @classmethod
    def _reject_blank_runtime_endpoint(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("runtime endpoint value must not be blank")
        return stripped

    @field_validator("agent_token_current", "agent_token_next")
    @classmethod
    def _reject_blank_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("token must not be blank")
        if value is not None and _BEARER_TOKEN_PATTERN.fullmatch(
            value.get_secret_value()
        ) is None:
            raise ValueError("token must use the RFC 6750 b64token alphabet")
        return value

    @model_validator(mode="after")
    def _validate_production_tokens(self) -> Settings:
        if self.environment_mode is not EnvironmentMode.PRODUCTION:
            return self
        current = self.agent_token_current.get_secret_value()
        if len(current) < MINIMUM_PRODUCTION_TOKEN_LENGTH:
            raise ValueError(
                "agent_token_current must contain at least "
                f"{MINIMUM_PRODUCTION_TOKEN_LENGTH} characters in production"
            )
        if self.agent_token_next is not None:
            next_token = self.agent_token_next.get_secret_value()
            if len(next_token) < MINIMUM_PRODUCTION_TOKEN_LENGTH:
                raise ValueError(
                    "agent_token_next must contain at least "
                    f"{MINIMUM_PRODUCTION_TOKEN_LENGTH} characters in production"
                )
        if self.agent_sha == "0" * 40:
            raise ValueError("agent_sha must identify the deployed commit")
        if _XRAY_VERSION_PATTERN.fullmatch(self.xray_version) is None:
            raise ValueError("xray_version must be a pinned numeric N.N.N release")
        if self.xray_image_digest == "sha256:" + "0" * 64:
            raise ValueError("xray_image_digest must identify the deployed image")
        return self


__all__ = ("EnvironmentMode", "MINIMUM_PRODUCTION_TOKEN_LENGTH", "Settings")
