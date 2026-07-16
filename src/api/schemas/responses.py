from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _ResponseDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthDTO(_ResponseDTO):
    contract_version: Literal["v1"] = "v1"
    schema_version: Literal["1.0"] = "1.0"
    agent_sha: str
    xray_version: str
    xray_image_digest: str
    readiness: Literal["READY", "NOT_READY", "RECOVERY_READY"]
    applied_snapshot_revision: int | None
    applied_snapshot_hash: str | None


class AppliedSnapshotDTO(_ResponseDTO):
    contract_version: Literal["v1"] = "v1"
    schema_version: Literal["1.0"] = "1.0"
    snapshot_revision: int | None
    snapshot_hash: str | None


class ApplyResultDTO(_ResponseDTO):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_revision: int
    snapshot_hash: str
    result: Literal["applied", "no_op"]


__all__ = ("AppliedSnapshotDTO", "ApplyResultDTO", "HealthDTO")
