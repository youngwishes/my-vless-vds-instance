from __future__ import annotations

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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vless_node_id: NodeId
    environment_mode: EnvironmentMode = EnvironmentMode.PRODUCTION
    agent_token_current: SecretStr | None = None
    agent_token_next: SecretStr | None = None

    @field_validator("agent_token_current", "agent_token_next", mode="before")
    @classmethod
    def _reject_blank_token(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("token must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_production_tokens(self) -> Settings:
        if self.environment_mode is not EnvironmentMode.PRODUCTION:
            return self
        if self.agent_token_current is None:
            raise ValueError("agent_token_current is required in production")
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
