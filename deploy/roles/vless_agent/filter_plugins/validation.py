from __future__ import annotations

from ipaddress import AddressValueError, IPv4Address
from typing import Any, Mapping


def all_node_tokens_unique(value: object) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False

    tokens: list[str] = []
    for node_tokens in value.values():
        if not isinstance(node_tokens, Mapping):
            return False
        current = node_tokens.get("current")
        next_token = node_tokens.get("next", "")
        if not isinstance(current, str) or not current:
            return False
        if not isinstance(next_token, str):
            return False
        tokens.append(current)
        if next_token:
            tokens.append(next_token)
    return len(tokens) == len(set(tokens))


def is_unicast_ipv4(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        address = IPv4Address(value)
    except AddressValueError:
        return False
    return not address.is_unspecified and not address.is_multicast and int(address) != 0xFFFFFFFF


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {
            "vless_agent_all_node_tokens_unique": all_node_tokens_unique,
            "vless_agent_unicast_ipv4": is_unicast_ipv4,
        }


__all__ = ("FilterModule", "all_node_tokens_unique", "is_unicast_ipv4")
