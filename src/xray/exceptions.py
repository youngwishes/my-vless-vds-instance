from __future__ import annotations


class XrayError(RuntimeError):
    """The managed Xray runtime operation failed."""


class XrayTimeoutError(XrayError):
    """The Xray runtime did not respond before the configured deadline."""


class XrayUnavailableError(XrayError):
    """The Xray runtime is unavailable."""


class XrayProtocolError(XrayError):
    """The Xray runtime rejected or returned an invalid protocol message."""


__all__ = (
    "XrayError",
    "XrayProtocolError",
    "XrayTimeoutError",
    "XrayUnavailableError",
)
