from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


_CANONICAL_OPENAPI_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "contracts"
    / "v1"
    / "agent-v1.openapi.yaml"
)


def contract_v1_openapi() -> dict[str, Any]:
    document = yaml.safe_load(_CANONICAL_OPENAPI_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("canonical OpenAPI document is invalid")
    return deepcopy(document)


__all__ = ("contract_v1_openapi",)
