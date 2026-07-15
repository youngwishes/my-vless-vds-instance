from __future__ import annotations

from dataclasses import dataclass


@dataclass(kw_only=True, slots=True, frozen=True)
class XrayUser:
    email: str
    uuid: str


def access_email(access_id: int) -> str:
    return f"vless-access-{access_id}@agent.invalid"


__all__ = ("XrayUser", "access_email")
