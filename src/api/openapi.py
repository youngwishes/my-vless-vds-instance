from __future__ import annotations

from typing import Any


def contract_v1_openapi() -> dict[str, Any]:
    operation = lambda operation_id, statuses, response_schema: {
        "operationId": operation_id,
        "parameters": [{"$ref": "#/components/parameters/ContractVersion"}],
        "responses": {
            status: (
                {"$ref": "#/components/responses/Unauthorized"}
                if status == "401"
                else {"$ref": "#/components/responses/IncompatibleContract"}
                if status == "426"
                else {
                    "description": "Successful response.",
                    "content": {"application/json": {"schema": {"$ref": response_schema}}},
                }
            )
            for status in statuses
        },
    }
    health = operation("getHealthV1", ("200", "401", "426"), "#/components/schemas/Health")
    get_snapshot = operation("getAppliedSnapshotV1", ("200", "401", "426"), "#/components/schemas/AppliedSnapshot")
    put_snapshot = operation("putExactSnapshotV1", ("200", "401", "426"), "#/components/schemas/ApplyResult")
    health["responses"]["200"]["description"] = "Runtime, Xray, and applied exact-snapshot readiness."
    get_snapshot["responses"]["200"]["description"] = "Metadata for the durable applied snapshot; UUIDs are not returned."
    put_snapshot["responses"]["200"]["description"] = "Snapshot durably applied, or equal revision/hash no-op."
    for contract_operation in (health, get_snapshot, put_snapshot):
        contract_operation["responses"]["426"]["x-error-codes"] = ["incompatible_contract"]
    put_snapshot["requestBody"] = {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": "./snapshot-v1.schema.json"}}},
    }
    put_snapshot["responses"]["409"] = {
        "description": "Monotonic revision violation; no mutation is performed.",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SafeError"}}},
    }
    put_snapshot["responses"]["413"] = {
        "description": "Entry or canonical-byte maximum exceeded before mutation.",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SafeError"}}},
    }
    hash_schema = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    safe_error = {
        "type": "object", "additionalProperties": False,
        "required": ["code", "message"],
        "properties": {
            "code": {"type": "string", "enum": [
                "unauthorized", "stale_revision", "revision_conflict",
                "snapshot_too_large", "incompatible_contract",
            ]},
            "message": {"type": "string"},
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "VLESS node agent contract", "version": "1.0.0"},
        "security": [{"bearerAuth": []}],
        "paths": {
            "/api/v1/health": {"get": health},
            "/api/v1/snapshot": {"get": get_snapshot, "put": put_snapshot},
        },
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "parameters": {"ContractVersion": {
                "name": "X-Agent-Contract-Version", "in": "header", "required": True,
                "schema": {"type": "string", "const": "v1"},
            }},
            "schemas": {
                "Hash": hash_schema,
                "Health": {
                    "type": "object", "additionalProperties": False,
                    "required": ["contract_version", "schema_version", "agent_sha", "xray_version", "xray_image_digest", "readiness", "applied_snapshot_revision", "applied_snapshot_hash"],
                    "properties": {
                        "contract_version": {"const": "v1"}, "schema_version": {"const": "1.0"},
                        "agent_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                        "xray_version": {"type": "string"},
                        "xray_image_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                        "readiness": {"type": "string", "enum": ["READY", "NOT_READY", "RECOVERY_READY"]},
                        "applied_snapshot_revision": {"type": ["integer", "null"], "minimum": 1},
                        "applied_snapshot_hash": {"oneOf": [{"$ref": "#/components/schemas/Hash"}, {"type": "null"}]},
                    },
                },
                "AppliedSnapshot": {
                    "type": "object", "additionalProperties": False,
                    "required": ["contract_version", "schema_version", "snapshot_revision", "snapshot_hash"],
                    "properties": {
                        "contract_version": {"const": "v1"}, "schema_version": {"const": "1.0"},
                        "snapshot_revision": {"type": ["integer", "null"], "minimum": 1},
                        "snapshot_hash": {"oneOf": [{"$ref": "#/components/schemas/Hash"}, {"type": "null"}]},
                    },
                },
                "ApplyResult": {
                    "type": "object", "additionalProperties": False,
                    "required": ["schema_version", "snapshot_revision", "snapshot_hash", "result"],
                    "properties": {
                        "schema_version": {"const": "1.0"},
                        "snapshot_revision": {"type": "integer", "minimum": 1},
                        "snapshot_hash": {"$ref": "#/components/schemas/Hash"},
                        "result": {"type": "string", "enum": ["applied", "no_op"]},
                    },
                },
                "SafeError": safe_error,
            },
            "responses": {
                "Unauthorized": {
                    "description": "Missing or invalid bearer authentication.",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SafeError"}}},
                },
                "IncompatibleContract": {
                    "description": "Unknown contract or snapshot schema major; no mutation is performed.",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SafeError"}}},
                },
            },
        },
        "x-response-matrix": [
            {"request": "GET /api/v1/health", "outcomes": ["200", "401", "426 incompatible_contract"]},
            {"request": "GET /api/v1/snapshot", "outcomes": ["200", "401", "426 incompatible_contract"]},
            {"request": "PUT /api/v1/snapshot", "outcomes": [
                "200 applied", "200 no_op", "401 unauthorized", "409 stale_revision",
                "409 revision_conflict", "413 snapshot_too_large", "426 incompatible_contract",
            ]},
        ],
    }


__all__ = ("contract_v1_openapi",)
