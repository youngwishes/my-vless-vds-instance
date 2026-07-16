from __future__ import annotations

from fastapi import APIRouter, Request, Response

from src.api.routes.dependencies import AUTHORIZED_V1, ERROR_RESPONSES
from src.api.schemas import HealthDTO


router = APIRouter()


@router.get(
    "/api/v1/health",
    dependencies=list(AUTHORIZED_V1),
    operation_id="getHealthV1",
    response_model=HealthDTO,
    responses=ERROR_RESPONSES,
)
def get_health(request: Request) -> HealthDTO | Response:
    try:
        health = request.app.state.services.get_health()
    except Exception:
        return Response(status_code=500, content="Internal Server Error", media_type="text/plain")
    snapshot = health.snapshot
    return HealthDTO(
        agent_sha=health.agent_sha,
        xray_version=health.xray_version,
        xray_image_digest=health.xray_image_digest,
        readiness=health.readiness.value,
        applied_snapshot_revision=(snapshot.snapshot_revision if snapshot else None),
        applied_snapshot_hash=(snapshot.snapshot_hash if snapshot else None),
    )


__all__ = ("router",)
