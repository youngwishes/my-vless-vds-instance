from __future__ import annotations

import json
import re
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


def has_durable_snapshot_volume(value: object, expected_name: object) -> bool:
    if not isinstance(value, Mapping) or not isinstance(expected_name, str):
        return False
    try:
        mounts = value["services"]["agent"]["volumes"]
        volume_definition = value["volumes"]["agent-snapshot"]
    except (KeyError, TypeError):
        return False
    if not isinstance(mounts, list) or not isinstance(volume_definition, Mapping):
        return False
    target_mounts = [
        mount
        for mount in mounts
        if isinstance(mount, Mapping)
        and mount.get("target") == "/var/lib/vless-agent"
    ]
    matching_mounts = [
        mount
        for mount in target_mounts
        if mount.get("type") == "volume"
        and mount.get("source") == "agent-snapshot"
        and mount.get("read_only", False) is False
    ]
    return (
        len(target_mounts) == 1
        and len(matching_mounts) == 1
        and volume_definition.get("name") == expected_name
    )


def nginx_listeners(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    sanitized: list[str] = []
    quote: str | None = None
    escaped = False
    comment = False
    for character in value:
        if comment:
            if character == "\n":
                comment = False
                sanitized.append(character)
            else:
                sanitized.append(" ")
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            sanitized.append("\n" if character == "\n" else " ")
            continue
        if character == "#":
            comment = True
            sanitized.append(" ")
        elif character in ("'", '"'):
            quote = character
            sanitized.append(" ")
        else:
            sanitized.append(character)
    listeners: list[str] = []
    pattern = re.compile(
        r"(?m)(?=(?:^[ \t]*listen\s+([^;{}]+);|[;{}]\s*listen\s+([^;{}]+);))"
    )
    for match in pattern.finditer("".join(sanitized)):
        directive = match.group(1) if match.group(1) is not None else match.group(2)
        listeners.append(" ".join(directive.split()))
    return listeners


def compose_runtime_was_running(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        containers = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return False
    if isinstance(containers, Mapping):
        containers = [containers]
    if not isinstance(containers, list):
        return False
    states = {
        container.get("Service"): str(container.get("State", "")).lower()
        for container in containers
        if isinstance(container, Mapping)
    }
    return states.get("agent") == "running" and states.get("xray") == "running"


def safe_packaged_nginx_default(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("exists") is False:
        return True
    return (
        value.get("exists") is True
        and value.get("islnk") is True
        and value.get("lnk_source") == "/etc/nginx/sites-available/default"
    )


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {
            "vless_agent_all_node_tokens_unique": all_node_tokens_unique,
            "vless_agent_compose_runtime_was_running": compose_runtime_was_running,
            "vless_agent_has_durable_snapshot_volume": has_durable_snapshot_volume,
            "vless_agent_nginx_listeners": nginx_listeners,
            "vless_agent_safe_packaged_nginx_default": safe_packaged_nginx_default,
            "vless_agent_unicast_ipv4": is_unicast_ipv4,
        }


__all__ = (
    "FilterModule",
    "all_node_tokens_unique",
    "compose_runtime_was_running",
    "has_durable_snapshot_volume",
    "is_unicast_ipv4",
    "nginx_listeners",
    "safe_packaged_nginx_default",
)
