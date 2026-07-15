"""Exact-set adapter for the one agent-owned Xray inbound."""

from src.xray.client import GrpcXrayClient, XrayClient
from src.xray.dtos import VLESS_VISION_FLOW, XrayUser, access_email
from src.xray.exact_set_service import ApplyExactSetService, create_apply_exact_set_service
from src.xray.exact_set_probe import ExactSetMatchesService
from src.xray.exceptions import (
    XrayError,
    XrayProtocolError,
    XrayTimeoutError,
    XrayUnavailableError,
)

__all__ = (
    "ApplyExactSetService",
    "ExactSetMatchesService",
    "GrpcXrayClient",
    "XrayClient",
    "XrayError",
    "XrayProtocolError",
    "XrayTimeoutError",
    "XrayUnavailableError",
    "XrayUser",
    "VLESS_VISION_FLOW",
    "access_email",
    "create_apply_exact_set_service",
)
