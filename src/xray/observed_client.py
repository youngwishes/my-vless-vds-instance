from __future__ import annotations

from dataclasses import dataclass
from typing import final

from src.observability import EventCode, Observability
from src.xray.client import XrayClient
from src.xray.dtos import XrayUser
from src.xray.exceptions import (
    XrayProtocolError,
    XrayTimeoutError,
    XrayUnavailableError,
)


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class ObservedXrayClient:
    client: XrayClient
    observer: Observability

    def get_inbound_users(self, *, tag: str) -> tuple[XrayUser, ...]:
        try:
            return self.client.get_inbound_users(tag=tag)
        except BaseException as error:
            self._record(error=error)
            raise

    def add_user(self, *, tag: str, user: XrayUser) -> None:
        try:
            self.client.add_user(tag=tag, user=user)
        except BaseException as error:
            self._record(error=error)
            raise

    def remove_user(self, *, tag: str, email: str) -> None:
        try:
            self.client.remove_user(tag=tag, email=email)
        except BaseException as error:
            self._record(error=error)
            raise

    def _record(self, *, error: BaseException) -> None:
        if isinstance(error, XrayTimeoutError):
            self.observer.record(EventCode.XRAY_TIMEOUT)
        elif isinstance(error, XrayUnavailableError):
            self.observer.record(EventCode.XRAY_UNAVAILABLE)
        elif isinstance(error, XrayProtocolError):
            self.observer.record(EventCode.XRAY_PROTOCOL_FAILURE)


__all__ = ("ObservedXrayClient",)
