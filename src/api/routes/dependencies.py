from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from src.api.schemas import SafeErrorDTO
from src.security import require_bearer_token


class IncompatibleContractError(Exception):
    """The requested contract major is unsupported."""


def require_contract_version(
    x_agent_contract_version: Annotated[
        str | None, Header(alias="X-Agent-Contract-Version")
    ] = None,
) -> None:
    if x_agent_contract_version != "v1":
        raise IncompatibleContractError


AUTHORIZED_V1 = (Depends(require_bearer_token), Depends(require_contract_version))
ERROR_RESPONSES = {
    401: {"model": SafeErrorDTO, "description": "Missing or invalid bearer authentication."},
    426: {"model": SafeErrorDTO, "description": "Unknown contract or snapshot schema major; no mutation is performed."},
}


__all__ = (
    "AUTHORIZED_V1",
    "ERROR_RESPONSES",
    "IncompatibleContractError",
    "require_contract_version",
)
