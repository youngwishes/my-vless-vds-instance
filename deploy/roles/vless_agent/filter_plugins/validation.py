from __future__ import annotations

import json
import re
from ipaddress import AddressValueError, IPv4Address, IPv4Network, ip_network
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
        if not isinstance(current, str):
            return False
        if not isinstance(next_token, str):
            return False
        current = current.strip()
        next_token = next_token.strip()
        if not current:
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


_MANAGEMENT_NETWORK = IPv4Network("172.31.255.0/28")
_MANAGEMENT_GATEWAY = "172.31.255.1"
_XRAY_ADDRESS = "172.31.255.2"
_AGENT_ADDRESS = "172.31.255.3"
_XRAY_IMAGE = (
    "ghcr.io/xtls/xray-core@"
    "sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f"
)


def valid_rendered_topology(value: object, expected_volume: object) -> bool:
    if not isinstance(value, Mapping) or not isinstance(expected_volume, str):
        return False
    try:
        services = value["services"]
        networks = value["networks"]
        xray = services["xray"]
        agent = services["agent"]
        management = networks["management"]
        ipam_config = management["ipam"]["config"]
    except (KeyError, TypeError):
        return False
    if not all(
        isinstance(item, Mapping)
        for item in (services, networks, xray, agent, management)
    ):
        return False
    return (
        management.get("internal") is True
        and isinstance(ipam_config, list)
        and ipam_config
        == [{"subnet": str(_MANAGEMENT_NETWORK), "gateway": _MANAGEMENT_GATEWAY}]
        and xray.get("image") == _XRAY_IMAGE
        and xray.get("networks")
        == {
            "management": {"ipv4_address": _XRAY_ADDRESS},
            "public": {},
        }
        and agent.get("networks")
        == {"management": {"ipv4_address": _AGENT_ADDRESS}}
        and "ports" not in agent
        and has_durable_snapshot_volume(value, expected_volume)
    )


def _canonical_builtin_without_subnets(value: Mapping[str, object]) -> bool:
    ipam = value.get("IPAM")
    return (
        (value.get("Name"), value.get("Driver"))
        in {("host", "host"), ("none", "null")}
        and value.get("Scope") == "local"
        and value.get("EnableIPv4") is True
        and value.get("EnableIPv6") is False
        and isinstance(ipam, Mapping)
        and dict(ipam)
        == {"Driver": "default", "Options": None, "Config": None}
        and value.get("Internal") is False
        and value.get("Attachable") is False
        and value.get("Ingress") is False
        and value.get("ConfigOnly") is False
        and value.get("Options") == {}
        and value.get("Labels") == {}
        and isinstance(value.get("Containers"), Mapping)
    )


def _docker_network_ipv4_subnets(value: Mapping[str, object]) -> list[IPv4Network] | None:
    ipam = value.get("IPAM")
    if not isinstance(ipam, Mapping):
        return None
    configs = ipam.get("Config")
    if value.get("Name") in {"host", "none"} or value.get("Driver") in {
        "host",
        "null",
    }:
        return [] if _canonical_builtin_without_subnets(value) else None
    if not isinstance(configs, list):
        return None
    subnets: list[IPv4Network] = []
    for config in configs:
        if not isinstance(config, Mapping):
            return None
        subnet = config.get("Subnet")
        if not isinstance(subnet, str):
            return None
        try:
            parsed = ip_network(subnet, strict=False)
        except ValueError:
            return None
        if isinstance(parsed, IPv4Network):
            subnets.append(parsed)
    return subnets


def _expected_network_bridge(
    network: Mapping[str, object], expected_project: str
) -> str | None:
    expected_name = f"{expected_project}_management"
    labels = network.get("Labels")
    ipam = network.get("IPAM")
    if not isinstance(labels, Mapping) or not isinstance(ipam, Mapping):
        return None
    ipam_options = ipam.get("Options")
    if (
        ipam.get("Driver") != "default"
        or "Options" not in ipam
        or (
            ipam_options is not None
            and (not isinstance(ipam_options, Mapping) or bool(ipam_options))
        )
    ):
        return None
    configs = ipam.get("Config")
    options = network.get("Options")
    allowed_options = {
        "com.docker.network.enable_ipv4": "true",
        "com.docker.network.enable_ipv6": "false",
    }
    if not isinstance(options, Mapping) or any(
        key not in allowed_options or value != allowed_options[key]
        for key, value in options.items()
    ):
        return None
    if not isinstance(configs, list) or len(configs) != 1:
        return None
    config = configs[0]
    if (
        not isinstance(config, Mapping)
        or set(config) not in (
            {"Subnet", "Gateway"},
            {"Subnet", "Gateway", "IPRange"},
        )
        or config.get("Subnet") != str(_MANAGEMENT_NETWORK)
        or config.get("Gateway") != _MANAGEMENT_GATEWAY
        or ("IPRange" in config and config.get("IPRange") != "")
    ):
        return None
    if (
        network.get("Name") != expected_name
        or network.get("Driver") != "bridge"
        or network.get("Internal") is not True
        or labels.get("com.docker.compose.project") != expected_project
        or labels.get("com.docker.compose.network") != "management"
    ):
        return None
    network_id = network.get("Id")
    if not isinstance(network_id, str) or not re.fullmatch(r"[0-9a-f]{12,}", network_id):
        return None
    return f"br-{network_id[:12]}"


def _container_inspects_by_id(
    container_inspects: object,
) -> dict[str, Mapping[str, object]] | None:
    if not isinstance(container_inspects, list):
        return None
    inspected_by_id: dict[str, Mapping[str, object]] = {}
    for inspected in container_inspects:
        if not isinstance(inspected, Mapping):
            return None
        container_id = inspected.get("Id")
        config = inspected.get("Config")
        settings = inspected.get("NetworkSettings")
        if (
            not isinstance(container_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
            or container_id in inspected_by_id
            or not isinstance(inspected.get("Name"), str)
            or not isinstance(config, Mapping)
            or (
                config.get("Labels") is not None
                and not isinstance(config.get("Labels"), Mapping)
            )
            or not isinstance(settings, Mapping)
            or not isinstance(settings.get("Networks"), Mapping)
        ):
            return None
        inspected_by_id[container_id] = inspected
    return inspected_by_id


def _reserved_endpoints_safe(
    network: Mapping[str, object],
    inspected_by_id: Mapping[str, Mapping[str, object]],
    expected_project: str,
) -> bool:
    containers = network.get("Containers")
    if not isinstance(containers, Mapping):
        return False

    expected_services = {
        _XRAY_ADDRESS: "xray",
        _AGENT_ADDRESS: "agent",
    }
    seen_reserved: set[str] = set()
    for container_id, endpoint in containers.items():
        if not isinstance(container_id, str) or not isinstance(endpoint, Mapping):
            return False
        address = endpoint.get("IPv4Address")
        if not isinstance(endpoint.get("Name"), str) or not isinstance(address, str):
            return False
        try:
            parsed = address.split("/", 1)[0]
            IPv4Address(parsed)
        except (AddressValueError, ValueError):
            return False
        service = expected_services.get(parsed)
        if service is None:
            continue
        if parsed in seen_reserved:
            return False
        seen_reserved.add(parsed)
        inspected = inspected_by_id.get(container_id)
        if inspected is None:
            return False
        config = inspected.get("Config")
        settings = inspected.get("NetworkSettings")
        if not isinstance(config, Mapping) or not isinstance(settings, Mapping):
            return False
        labels = config.get("Labels")
        memberships = settings.get("Networks")
        expected_network = f"{expected_project}_management"
        if not isinstance(labels, Mapping) or not isinstance(memberships, Mapping):
            return False
        membership = memberships.get(expected_network)
        expected_memberships = {expected_network}
        if service == "xray":
            expected_memberships.add(f"{expected_project}_public")
        if (
            inspected.get("Name") != f"/{expected_project}-{service}-1"
            or labels.get("com.docker.compose.project") != expected_project
            or labels.get("com.docker.compose.service") != service
            or set(memberships) != expected_memberships
            or not isinstance(membership, Mapping)
            or membership.get("IPAddress") != parsed
        ):
            return False
    return True


def network_preflight_safe(
    addresses: object,
    routes: object,
    networks: object,
    container_inspects: object,
    expected_project: object,
) -> bool:
    if (
        not isinstance(addresses, list)
        or not isinstance(routes, list)
        or not isinstance(networks, list)
        or not isinstance(container_inspects, list)
        or not isinstance(expected_project, str)
        or not expected_project
    ):
        return False

    inspected_by_id = _container_inspects_by_id(container_inspects)
    if inspected_by_id is None:
        return False

    expected_name = f"{expected_project}_management"
    expected_network: Mapping[str, object] | None = None
    for network in networks:
        if not isinstance(network, Mapping):
            return False
        subnets = _docker_network_ipv4_subnets(network)
        if subnets is None:
            return False
        overlaps = any(subnet.overlaps(_MANAGEMENT_NETWORK) for subnet in subnets)
        if network.get("Name") == expected_name:
            if expected_network is not None:
                return False
            expected_network = network
            if _expected_network_bridge(network, expected_project) is None:
                return False
            if not _reserved_endpoints_safe(
                network, inspected_by_id, expected_project
            ):
                return False
        elif overlaps:
            return False

    bridge = (
        _expected_network_bridge(expected_network, expected_project)
        if expected_network is not None
        else None
    )
    for interface in addresses:
        if not isinstance(interface, Mapping) or not isinstance(interface.get("ifname"), str):
            return False
        details = interface.get("addr_info")
        if not isinstance(details, list):
            return False
        for address in details:
            if not isinstance(address, Mapping):
                return False
            if address.get("family") != "inet":
                continue
            local = address.get("local")
            prefixlen = address.get("prefixlen")
            if not isinstance(local, str) or not isinstance(prefixlen, int):
                return False
            try:
                subnet = IPv4Network(f"{local}/{prefixlen}", strict=False)
            except ValueError:
                return False
            if subnet.overlaps(_MANAGEMENT_NETWORK) and not (
                bridge is not None
                and interface["ifname"] == bridge
                and local == _MANAGEMENT_GATEWAY
                and prefixlen == _MANAGEMENT_NETWORK.prefixlen
            ):
                return False

    allowed_route_destinations = {
        str(_MANAGEMENT_NETWORK),
        _MANAGEMENT_GATEWAY,
        str(_MANAGEMENT_NETWORK.network_address),
        str(_MANAGEMENT_NETWORK.broadcast_address),
    }
    for route in routes:
        if not isinstance(route, Mapping):
            return False
        destination = route.get("dst")
        if destination == "default":
            continue
        if not isinstance(destination, str) or not isinstance(route.get("dev"), str):
            return False
        try:
            subnet = ip_network(destination, strict=False)
        except ValueError:
            return False
        if isinstance(subnet, IPv4Network) and subnet.overlaps(_MANAGEMENT_NETWORK):
            normalized = destination.split("/", 1)[0] if subnet.prefixlen == 32 else str(subnet)
            if not (
                bridge is not None
                and route["dev"] == bridge
                and normalized in allowed_route_destinations
            ):
                return False
    return True


def _single_inspect(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        return None
    return value[0]


def _runtime_container_valid(
    container: Mapping[str, object],
    *,
    project: str,
    service: str,
    expected_networks: Mapping[str, str],
) -> bool:
    config = container.get("Config")
    settings = container.get("NetworkSettings")
    host_config = container.get("HostConfig")
    if not all(isinstance(item, Mapping) for item in (config, settings, host_config)):
        return False
    labels = config.get("Labels")
    memberships = settings.get("Networks")
    if not isinstance(labels, Mapping) or not isinstance(memberships, Mapping):
        return False
    name = container.get("Name")
    if (
        not isinstance(name, str)
        or re.fullmatch(rf"/?{re.escape(project)}-{service}-[1-9][0-9]*", name) is None
        or labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != service
        or set(memberships) != set(expected_networks)
    ):
        return False
    for network_name, expected_address in expected_networks.items():
        network = memberships.get(network_name)
        if not isinstance(network, Mapping) or network.get("IPAddress") != expected_address:
            return False
    if service == "agent":
        exposed_ports = settings.get("Ports")
        if not (
            exposed_ports in (None, {})
            or (
                isinstance(exposed_ports, Mapping)
                and all(bindings in (None, []) for bindings in exposed_ports.values())
            )
        ):
            return False
        if host_config.get("PortBindings") not in (None, {}):
            return False
    return True


def valid_runtime_topology(
    network_inspect: object,
    xray_inspect: object,
    agent_inspect: object,
    expected_project: object,
) -> bool:
    if not isinstance(expected_project, str) or not expected_project:
        return False
    network = _single_inspect(network_inspect)
    xray = _single_inspect(xray_inspect)
    agent = _single_inspect(agent_inspect)
    if network is None or xray is None or agent is None:
        return False
    if _expected_network_bridge(network, expected_project) is None:
        return False
    containers = network.get("Containers")
    if not isinstance(containers, Mapping) or len(containers) != 2:
        return False
    endpoints = {
        endpoint.get("Name"): endpoint.get("IPv4Address")
        for endpoint in containers.values()
        if isinstance(endpoint, Mapping)
    }
    if endpoints != {
        f"{expected_project}-xray-1": f"{_XRAY_ADDRESS}/28",
        f"{expected_project}-agent-1": f"{_AGENT_ADDRESS}/28",
    }:
        return False
    xray_settings = xray.get("NetworkSettings")
    if not isinstance(xray_settings, Mapping):
        return False
    xray_networks = xray_settings.get("Networks")
    if not isinstance(xray_networks, Mapping):
        return False
    public_endpoint = xray_networks.get(f"{expected_project}_public")
    if not isinstance(public_endpoint, Mapping):
        return False
    public_address = public_endpoint.get("IPAddress")
    if not isinstance(public_address, str) or not public_address:
        return False
    return _runtime_container_valid(
        xray,
        project=expected_project,
        service="xray",
        expected_networks={
            f"{expected_project}_management": _XRAY_ADDRESS,
            f"{expected_project}_public": public_address,
        },
    ) and _runtime_container_valid(
        agent,
        project=expected_project,
        service="agent",
        expected_networks={f"{expected_project}_management": _AGENT_ADDRESS},
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
        r"(?m)(?=(?:^[ \t]*listen\s+([^;{}]+);|[;{}][ \t]*listen\s+([^;{}]+);))"
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
            "vless_agent_network_preflight_safe": network_preflight_safe,
            "vless_agent_nginx_listeners": nginx_listeners,
            "vless_agent_safe_packaged_nginx_default": safe_packaged_nginx_default,
            "vless_agent_unicast_ipv4": is_unicast_ipv4,
            "vless_agent_valid_rendered_topology": valid_rendered_topology,
            "vless_agent_valid_runtime_topology": valid_runtime_topology,
        }


__all__ = (
    "FilterModule",
    "all_node_tokens_unique",
    "compose_runtime_was_running",
    "has_durable_snapshot_volume",
    "is_unicast_ipv4",
    "network_preflight_safe",
    "nginx_listeners",
    "safe_packaged_nginx_default",
    "valid_rendered_topology",
    "valid_runtime_topology",
)
