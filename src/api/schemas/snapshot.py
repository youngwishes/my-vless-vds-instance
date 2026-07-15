from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator


PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]
CanonicalUUID = Annotated[
    StrictStr,
    Field(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
    ),
]
LowercaseSHA256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
SafeErrorCode = Literal[
    "unauthorized",
    "stale_revision",
    "revision_conflict",
    "snapshot_too_large",
    "incompatible_contract",
]


class _FrozenDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AccessDTO(_FrozenDTO):
    access_id: PositiveStrictInt
    uuid: CanonicalUUID
    access_revision: PositiveStrictInt


class SnapshotDTO(_FrozenDTO):
    schema_version: StrictStr
    snapshot_revision: PositiveStrictInt
    snapshot_hash: LowercaseSHA256
    accesses: tuple[AccessDTO, ...]

    @field_validator("accesses", mode="before")
    @classmethod
    def _freeze_accesses(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        return value


class SafeErrorDTO(_FrozenDTO):
    code: SafeErrorCode
    message: StrictStr


__all__ = ("AccessDTO", "SafeErrorDTO", "SnapshotDTO")
