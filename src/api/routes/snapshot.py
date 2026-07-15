from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from src.api.routes.dependencies import AUTHORIZED_V1, ERROR_RESPONSES
from src.api.schemas import AppliedSnapshotDTO, ApplyResultDTO, SafeErrorDTO, SnapshotDTO
from src.domain import MAX_CANONICAL_BYTES
from src.exceptions import IncompatibleSchemaVersionError, InvalidSnapshotError, SnapshotTooLargeError
from src.services import RevisionConflictError, StaleRevisionError


router = APIRouter()
_MAX_RAW_REQUEST_BYTES = MAX_CANONICAL_BYTES + 128


def _error(*, status_code: int, code: str, message: str) -> Response:
    return Response(
        status_code=status_code,
        media_type="application/json",
        content=json.dumps({"code": code, "message": message}, separators=(",", ":")),
    )


@router.get(
    "/api/v1/snapshot",
    dependencies=list(AUTHORIZED_V1),
    operation_id="getAppliedSnapshotV1",
    response_model=AppliedSnapshotDTO,
    responses=ERROR_RESPONSES,
)
def get_snapshot(request: Request) -> AppliedSnapshotDTO:
    snapshot = request.app.state.services.get_snapshot()
    return AppliedSnapshotDTO(
        snapshot_revision=snapshot.snapshot_revision if snapshot else None,
        snapshot_hash=snapshot.snapshot_hash if snapshot else None,
    )


@router.put(
    "/api/v1/snapshot",
    dependencies=list(AUTHORIZED_V1),
    operation_id="putExactSnapshotV1",
    response_model=ApplyResultDTO,
    responses={
        **ERROR_RESPONSES,
        409: {"model": SafeErrorDTO, "description": "Monotonic revision violation; no mutation is performed."},
        413: {"model": SafeErrorDTO, "description": "Entry or canonical-byte maximum exceeded before mutation."},
    },
)
async def put_snapshot(request: Request) -> ApplyResultDTO | Response:
    content_type, separator, parameters = request.headers.get("content-type", "").partition(";")
    if content_type.strip().lower() != "application/json" or (
        separator
        and any(
            not parameter.strip().lower().startswith("charset=")
            or not parameter.partition("=")[2].strip()
            for parameter in parameters.split(";")
        )
    ):
        return Response(status_code=415)
    body = bytearray()
    async for chunk in request.stream():
        remaining = _MAX_RAW_REQUEST_BYTES - len(body)
        if len(chunk) > remaining:
            return _error(
                status_code=413,
                code="snapshot_too_large",
                message="Snapshot exceeds the supported contract limits.",
            )
        body.extend(chunk)
    try:
        snapshot = SnapshotDTO.model_validate_json(body)
        result = await run_in_threadpool(
            request.app.state.services.put_snapshot,
            snapshot=snapshot,
        )
    except SnapshotTooLargeError:
        return _error(status_code=413, code="snapshot_too_large", message="Snapshot exceeds the supported contract limits.")
    except IncompatibleSchemaVersionError:
        return _error(status_code=426, code="incompatible_contract", message="The requested contract or schema major is not supported.")
    except StaleRevisionError:
        return _error(status_code=409, code="stale_revision", message="Snapshot revision is older than the applied revision.")
    except RevisionConflictError:
        return _error(status_code=409, code="revision_conflict", message="Snapshot revision already exists with a different hash.")
    except (ValidationError, InvalidSnapshotError, ValueError, json.JSONDecodeError):
        return Response(status_code=400)
    except Exception:
        return Response(status_code=500, content="Internal Server Error", media_type="text/plain")
    return ApplyResultDTO(
        schema_version=result.snapshot.schema_version,
        snapshot_revision=result.snapshot.snapshot_revision,
        snapshot_hash=result.snapshot.snapshot_hash,
        result=result.result.value,
    )


__all__ = ("router",)
