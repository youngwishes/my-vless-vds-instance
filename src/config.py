from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from pydantic import SecretStr, StringConstraints, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentMode(StrEnum):
    LOCAL = "local"
    PRODUCTION = "production"
    TEST = "test"


NodeId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
MINIMUM_PRODUCTION_TOKEN_LENGTH = 32
_BEARER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9\-._~+/]+={0,}")


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
        return self


__all__ = ("EnvironmentMode", "MINIMUM_PRODUCTION_TOKEN_LENGTH", "Settings")
