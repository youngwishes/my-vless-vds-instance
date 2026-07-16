from __future__ import annotations

from dataclasses import dataclass


VLESS_VISION_FLOW = "xtls-rprx-vision"


@dataclass(kw_only=True, slots=True, frozen=True)
class XrayUser:
    email: str
    uuid: str
    flow: str


def access_email(access_id: int) -> str:
    return f"vless-access-{access_id}@agent.invalid"


__all__ = ("VLESS_VISION_FLOW", "XrayUser", "access_email")
