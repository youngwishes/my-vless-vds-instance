from __future__ import annotations

import configparser
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
ROLE = DEPLOY / "roles" / "vless_agent"
XRAY_DIGEST = "sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f"
REVIEWED_AGENT_SHA = "564dc521016cc7463f7e7870ceb159b60883cccb"
MANAGEMENT_SUBNET = "172.31.255.0/28"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _yaml(path: Path) -> object:
    return yaml.safe_load(_read(path))


def _all_deploy_text() -> str:
    return "\n".join(
        _read(path)
        for path in sorted(DEPLOY.rglob("*"))
        if path.is_file()
        and "tests" not in path.parts
        and "__pycache__" not in path.parts
    )


def _load_role_filters() -> dict[str, object]:
    path = ROLE / "filter_plugins" / "validation.py"
    spec = importlib.util.spec_from_file_location("vless_agent_validation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FilterModule().filters()


def _walk_tasks(tasks: list[dict[str, object]]) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    for task in tasks:
        flattened.append(task)
        for section in ("block", "rescue", "always"):
            children = task.get(section, [])
            if isinstance(children, list):
                flattened.extend(_walk_tasks(children))
    return flattened


def _render_firewall(*, sources: list[str], port: int = 8443) -> str:
    template = _read(ROLE / "templates" / "vless-agent-firewall.sh.j2")
    rendered: list[str] = []
    source_line: str | None = None
    in_source_loop = False
    for line in template.splitlines():
        if line.startswith("{% for source in"):
            in_source_loop = True
            continue
        if line.startswith("{% endfor %}"):
            assert source_line is not None
            rendered.extend(source_line.replace("{{ source }}", source) for source in sources)
            in_source_loop = False
            source_line = None
            continue
        if in_source_loop:
            source_line = line
            continue
        rendered.append(line)
    return "\n".join(rendered).replace(
        "{{ vless_agent_firewall_active_port | default(vless_agent_management_port) }}",
        str(port),
    )


def _write_fake_iptables(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_IPTABLES_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {"chains": {"INPUT": []}}
args = [value for value in sys.argv[1:] if value != "-w"]
chains = state["chains"]

def save(code=0):
    state_path.write_text(json.dumps(state))
    raise SystemExit(code)

command = args[0]
if command == "-N":
    if args[1] in chains:
        save(1)
    chains[args[1]] = []
elif command == "-A":
    chains[args[1]].append(args[2:])
elif command == "-I":
    chains[args[1]].insert(int(args[2]) - 1, args[3:])
elif command == "-L":
    chain = args[1]
    if chain not in chains:
        save(1)
    if chain == "INPUT":
        for number, rule in enumerate(chains[chain], 1):
            target = rule[rule.index("-j") + 1]
            print(f"{number} {target} tcp -- anywhere anywhere")
elif command == "-nL":
    if args[1] not in chains:
        save(1)
elif command == "-D":
    chains[args[1]].pop(int(args[2]) - 1)
elif command == "-F":
    chains[args[1]] = []
elif command == "-X":
    target = args[1]
    if any(rule[rule.index("-j") + 1] == target for rule in chains["INPUT"]):
        save(4)
    del chains[target]
elif command == "-E":
    old, new = args[1:3]
    chains[new] = chains.pop(old)
    for rule in chains["INPUT"]:
        if rule[rule.index("-j") + 1] == old:
            rule[rule.index("-j") + 1] = new
else:
    save(3)
save()
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_playbooks_are_environment_specific_and_prod_is_serial_fail_fast() -> None:
    test_play = _yaml(DEPLOY / "playbook-test.yml")[0]
    prod_play = _yaml(DEPLOY / "playbook-prod.yml")[0]

    assert test_play["hosts"] == "vless_test"
    assert prod_play["hosts"] == "vless_prod"
    assert prod_play["serial"] == 1
    assert prod_play["any_errors_fatal"] is True
    assert test_play["roles"] == [{"role": "vless_agent"}]
    assert prod_play["roles"] == [{"role": "vless_agent"}]


@pytest.mark.parametrize("name", ["playbook-test.yml", "playbook-prod.yml"])
def test_playbook_requires_exact_lowercase_full_git_sha(name: str) -> None:
    play = _yaml(DEPLOY / name)[0]
    assertions = play["pre_tasks"][0]["ansible.builtin.assert"]

    assert "deploy_revision is match('^[0-9a-f]{40}$')" in assertions["that"]
    assert "full 40-character lowercase Git SHA" in assertions["fail_msg"]


def test_git_checkout_uses_only_exact_deploy_revision_and_rejects_dirty_tree() -> None:
    tasks = _read(ROLE / "tasks" / "main.yml")

    assert "git status --porcelain" in tasks
    assert "vless_checkout_status.stdout | length == 0" in tasks
    assert 'version: "{{ deploy_revision }}"' in tasks
    assert "force: false" in tasks
    assert not re.search(r"repo_branch|version:\s*(main|master|HEAD|latest)", tasks)


def test_inventory_and_ansible_config_are_safe_examples() -> None:
    parser = configparser.ConfigParser()
    parser.read(DEPLOY / "ansible.cfg")
    inventory = _read(DEPLOY / "inventory.example.ini")
    ignored = _read(DEPLOY / ".gitignore").splitlines()

    assert parser["defaults"].getboolean("host_key_checking") is True
    assert parser["defaults"]["inventory"] == "inventory.example.ini"
    assert "vless_test" in inventory and "vless_prod" in inventory
    assert ".invalid" in inventory
    assert "192.0.2." not in inventory
    assert "inventory.ini" in ignored
    assert "*.vault.yml" in ignored
    assert "*.pem" in ignored and "*.key" in ignored


def test_operator_group_vars_are_ignored_but_examples_remain_trackable() -> None:
    ignored_paths = (
        "deploy/group_vars/vless_test.yml",
        "deploy/group_vars/vless_prod.yml",
    )
    for path in ignored_paths:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", path],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0

    for path in (
        "deploy/group_vars/vless_test.example.yml",
        "deploy/group_vars/vless_prod.example.yml",
    ):
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", path],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 1


def test_per_node_vault_secrets_are_validated_without_logging() -> None:
    tasks = _read(ROLE / "tasks" / "main.yml")
    parsed_tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    validation_task = next(
        task
        for task in parsed_tasks
        if task.get("name") == "Validate per-node Vault values and fleet token uniqueness"
    )

    assert "vault_vless_agent_tokens[inventory_hostname]" in tasks
    assert "vault_reality_private_keys[inventory_hostname]" in tasks
    assert "vless_agent_token_current | length >= 32" in tasks
    assert "vless_agent_token_next | length == 0" in tasks
    assert "vless_agent_token_next != vless_agent_token_current" in tasks
    assert "no_log: true" in tasks
    assert "shared_token" not in tasks
    assert "vless_agent_all_node_tokens_unique" in tasks
    assert validation_task["no_log"] is True
    assert (
        "vault_vless_agent_tokens | vless_agent_all_node_tokens_unique"
        in validation_task["ansible.builtin.assert"]["that"]
    )


def test_all_nonempty_current_and_next_tokens_are_fleet_unique() -> None:
    validator = _load_role_filters()["vless_agent_all_node_tokens_unique"]

    assert validator(
        {
            "node-a": {"current": "a" * 32, "next": "b" * 32},
            "node-b": {"current": "c" * 32, "next": ""},
        }
    )
    assert not validator(
        {
            "node-a": {"current": "a" * 32, "next": "b" * 32},
            "node-b": {"current": "b" * 32, "next": ""},
        }
    )
    assert not validator(
        {
            "node-a": {"current": "a" * 32, "next": "b" * 32},
            "node-b": {"current": "c" * 32, "next": "b" * 32},
        }
    )


def test_vault_tokens_are_trimmed_before_rendering_and_uniqueness_validation() -> None:
    parsed_tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    resolution_task = next(
        task for task in parsed_tasks if task.get("name") == "Resolve per-node Vault values"
    )
    facts = resolution_task["ansible.builtin.set_fact"]

    assert facts["vless_agent_token_current"].endswith(".current | trim }}")
    assert facts["vless_agent_token_next"].endswith("default('') | trim }}")

    validator = _load_role_filters()["vless_agent_all_node_tokens_unique"]
    assert not validator(
        {
            "node-a": {"current": "a" * 32 + "\n", "next": ""},
            "node-b": {"current": "a" * 32, "next": ""},
        }
    )


def test_env_wires_revision_node_secrets_and_pinned_xray_evidence() -> None:
    env = _read(ROLE / "templates" / "agent.env.j2")
    tasks = _read(ROLE / "tasks" / "main.yml")

    assert "AGENT_SHA={{ vless_agent_active_revision }}" in env
    assert "VLESS_NODE_ID={{ inventory_hostname }}" in env
    assert "AGENT_TOKEN_CURRENT={{ vless_agent_token_current }}" in env
    assert "REALITY_PRIVATE_KEY_FILE={{ vless_agent_reality_key_path }}" in env
    assert "XRAY_VERSION=26.7.11" in env
    assert f"XRAY_IMAGE_DIGEST={XRAY_DIGEST}" in env
    assert 'mode: "0600"' in tasks
    assert "no_log: true" in tasks


def test_rendered_compose_is_checked_for_exact_private_topology_and_named_volume() -> None:
    tasks = _read(ROLE / "tasks" / "main.yml")

    assert "config, --format, json" in tasks
    assert "vless_agent_valid_rendered_topology(vless_agent_snapshot_volume)" in tasks
    assert "exact private topology, Xray digest, and snapshot volume" in tasks


def test_compose_snapshot_volume_validator_rejects_broken_topologies() -> None:
    validator = _load_role_filters()["vless_agent_has_durable_snapshot_volume"]
    expected_name = "vless-agent_agent-snapshot"
    valid = {
        "services": {
            "agent": {
                "volumes": [
                    {
                        "type": "volume",
                        "source": "agent-snapshot",
                        "target": "/var/lib/vless-agent",
                        "read_only": False,
                    }
                ]
            }
        },
        "volumes": {"agent-snapshot": {"name": expected_name}},
    }

    assert validator(valid, expected_name)
    for mutate in (
        lambda value: value["services"]["agent"].update(volumes=[]),
        lambda value: value["services"]["agent"]["volumes"][0].update(read_only=True),
        lambda value: value["services"]["agent"]["volumes"][0].update(source="other"),
        lambda value: value["services"]["agent"]["volumes"][0].update(target="/tmp"),
        lambda value: value["volumes"]["agent-snapshot"].update(name="other-volume"),
        lambda value: value["services"]["agent"]["volumes"].append(
            {"type": "bind", "source": "/tmp", "target": "/var/lib/vless-agent"}
        ),
    ):
        broken = json.loads(json.dumps(valid))
        mutate(broken)
        assert not validator(broken, expected_name)


def _rendered_compose_topology() -> dict[str, object]:
    return {
        "services": {
            "xray": {
                "image": "ghcr.io/xtls/xray-core@" + XRAY_DIGEST,
                "networks": {
                    "management": {"ipv4_address": "172.31.255.2"},
                    "public": {},
                },
            },
            "agent": {
                "networks": {
                    "management": {"ipv4_address": "172.31.255.3"},
                },
                "volumes": [
                    {
                        "type": "volume",
                        "source": "agent-snapshot",
                        "target": "/var/lib/vless-agent",
                        "read_only": False,
                    }
                ],
            },
        },
        "networks": {
            "management": {
                "internal": True,
                "ipam": {
                    "config": [
                        {"subnet": MANAGEMENT_SUBNET, "gateway": "172.31.255.1"}
                    ]
                },
            },
            "public": {},
        },
        "volumes": {"agent-snapshot": {"name": "vless-agent_agent-snapshot"}},
    }


def test_rendered_compose_topology_validator_is_exact_and_preserves_invariants() -> None:
    validator = _load_role_filters()["vless_agent_valid_rendered_topology"]
    valid = _rendered_compose_topology()

    assert validator(valid, "vless-agent_agent-snapshot")
    mutations = (
        lambda value: value["networks"]["management"].update(internal=False),
        lambda value: value["networks"]["management"]["ipam"]["config"][0].update(subnet="172.31.255.0/29"),
        lambda value: value["networks"]["management"]["ipam"]["config"][0].update(gateway="172.31.255.4"),
        lambda value: value["services"]["xray"]["networks"]["management"].update(ipv4_address="172.31.255.4"),
        lambda value: value["services"]["xray"]["networks"].pop("public"),
        lambda value: value["services"]["agent"]["networks"]["management"].update(ipv4_address="172.31.255.4"),
        lambda value: value["services"]["agent"]["networks"].update(public={}),
        lambda value: value["services"]["agent"].update(ports=[]),
        lambda value: value["services"]["agent"].update(ports=[{"published": "8000"}]),
        lambda value: value["services"]["xray"].update(image="xray:latest"),
        lambda value: value["volumes"]["agent-snapshot"].update(name="wrong"),
    )
    for mutate in mutations:
        broken = json.loads(json.dumps(valid))
        mutate(broken)
        assert not validator(broken, "vless-agent_agent-snapshot")


def _expected_network() -> dict[str, object]:
    return {
        "Name": "vless-agent_management",
        "Id": "abcdef0123456789",
        "Driver": "bridge",
        "Options": {},
        "Internal": True,
        "Labels": {
            "com.docker.compose.project": "vless-agent",
            "com.docker.compose.network": "management",
        },
        "IPAM": {
            "Config": [{"Subnet": MANAGEMENT_SUBNET, "Gateway": "172.31.255.1"}]
        },
        "Containers": {},
    }


@pytest.mark.parametrize(
    "conflict",
    [
        "172.31.255.0/28",
        "172.31.255.0/29",
        "172.31.254.0/23",
    ],
)
def test_network_preflight_rejects_equal_subset_and_superset_cidrs(conflict: str) -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    addresses = [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": str(conflict).split("/")[0], "prefixlen": int(conflict.split("/")[1])}]}]

    assert not validator(addresses, [], [], [], "vless-agent")
    assert not validator([], [{"dst": conflict, "dev": "eth0"}], [], [], "vless-agent")
    assert not validator([], [], [{"Name": "foreign", "IPAM": {"Config": [{"Subnet": conflict}]}}], [], "vless-agent")


def test_network_preflight_allows_adjacent_cidr_and_verified_expected_bridge() -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    expected = _expected_network()
    addresses = [
        {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "172.31.255.17", "prefixlen": 28}]},
        {"ifname": "br-abcdef012345", "addr_info": [{"family": "inet", "local": "172.31.255.1", "prefixlen": 28}]},
    ]
    routes = [
        {"dst": "172.31.255.16/28", "dev": "eth0"},
        {"dst": MANAGEMENT_SUBNET, "dev": "br-abcdef012345", "scope": "link"},
    ]

    assert validator(addresses, routes, [expected], [], "vless-agent")


@pytest.mark.parametrize(
    ("options", "ipam_extra"),
    [
        ({}, {}),
        ({"com.docker.network.enable_ipv4": "true"}, {"IPRange": ""}),
        ({"com.docker.network.enable_ipv6": "false"}, {}),
        (
            {
                "com.docker.network.enable_ipv4": "true",
                "com.docker.network.enable_ipv6": "false",
            },
            {"IPRange": ""},
        ),
    ],
)
def test_network_preflight_accepts_docker_29_normalized_bridge_inspect(
    options: dict[str, str], ipam_extra: dict[str, str]
) -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    network = _expected_network()
    network["Options"] = options
    network["IPAM"]["Config"][0].update(ipam_extra)
    addresses = [{"ifname": "br-abcdef012345", "addr_info": [{"family": "inet", "local": "172.31.255.1", "prefixlen": 28}]}]
    routes = [{"dst": MANAGEMENT_SUBNET, "dev": "br-abcdef012345"}]

    assert validator(addresses, routes, [network], [], "vless-agent")


@pytest.mark.parametrize(
    "options",
    [
        {"com.docker.network.bridge.name": "custom"},
        {"unknown": "value"},
        {"com.docker.network.enable_ipv4": "false"},
        {"com.docker.network.enable_ipv4": True},
        {"com.docker.network.enable_ipv6": "true"},
        {"com.docker.network.enable_ipv6": False},
        {
            "com.docker.network.enable_ipv4": "true",
            "com.docker.network.enable_ipv6": "false",
            "unknown": "value",
        },
    ],
)
def test_network_preflight_rejects_unreviewed_or_mistyped_network_options(
    options: dict[str, object],
) -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    network = _expected_network()
    network["Options"] = options

    assert not validator([], [], [network], [], "vless-agent")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda config: config.update(IPRange="172.31.255.4/30"),
        lambda config: config.update(Unknown="value"),
        lambda config: config.pop("Subnet"),
        lambda config: config.pop("Gateway"),
        lambda config: config.update(Subnet="172.31.255.0/29"),
        lambda config: config.update(Gateway="172.31.255.4"),
        lambda config: config.update(IPRange=None),
    ],
)
def test_network_preflight_rejects_normalized_ipam_drift(mutate: object) -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    network = _expected_network()
    mutate(network["IPAM"]["Config"][0])

    assert not validator([], [], [network], [], "vless-agent")


def test_network_preflight_rejects_missing_options() -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    network = _expected_network()
    network.pop("Options")

    assert not validator([], [], [network], [], "vless-agent")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(Name="wrong"),
        lambda value: value.update(Internal=False),
        lambda value: value.update(Driver="macvlan"),
        lambda value: value.update(Options={"com.docker.network.bridge.name": "managed"}),
        lambda value: value["Labels"].update({"com.docker.compose.project": "other"}),
        lambda value: value["Labels"].update({"com.docker.compose.network": "other"}),
        lambda value: value["IPAM"]["Config"][0].update(Subnet="172.31.255.0/29"),
        lambda value: value["IPAM"]["Config"][0].update(Gateway="172.31.255.4"),
    ],
)
def test_network_preflight_rejects_expected_network_ownership_or_topology_drift(mutate: object) -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    network = _expected_network()
    mutate(network)

    assert not validator([], [], [network], [], "vless-agent")


def _preflight_container(
    *, container_id: str, service: str, address: str, project: str = "vless-agent"
) -> dict[str, object]:
    networks = {
        "vless-agent_management": {"IPAddress": address.split("/", 1)[0]}
    }
    if service == "xray":
        networks["vless-agent_public"] = {"IPAddress": "172.20.0.2"}
    return {
        "Id": container_id,
        "Name": f"/{project}-{service}-1",
        "Config": {
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
            }
        },
        "NetworkSettings": {"Networks": networks},
    }


@pytest.mark.parametrize(
    ("service", "address"),
    [("xray", "172.31.255.2/28"), ("agent", "172.31.255.3/28")],
)
def test_network_preflight_checks_reserved_endpoint_inspect_identity(
    service: str, address: str
) -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    network = _expected_network()
    container_id = ("a" if service == "xray" else "b") * 64
    network["Containers"] = {
        container_id: {"Name": f"vless-agent-{service}-1", "IPv4Address": address}
    }
    containers = [
        _preflight_container(
            container_id=container_id, service=service, address=address
        )
    ]
    addresses = [{"ifname": "br-abcdef012345", "addr_info": [{"family": "inet", "local": "172.31.255.1", "prefixlen": 28}]}]
    routes = [{"dst": MANAGEMENT_SUBNET, "dev": "br-abcdef012345"}]

    assert validator(addresses, routes, [network], containers, "vless-agent")


def test_network_preflight_rejects_similarly_named_foreign_reserved_occupant() -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    network = _expected_network()
    container_id = "c" * 64
    network["Containers"] = {
        container_id: {
            "Name": "vless-agent-xray-1",
            "IPv4Address": "172.31.255.2/28",
        }
    }
    spoof = _preflight_container(
        container_id=container_id,
        service="xray",
        address="172.31.255.2/28",
        project="foreign",
    )

    assert not validator([], [], [network], [spoof], "vless-agent")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["Config"]["Labels"].update(
            {"com.docker.compose.service": "agent"}
        ),
        lambda value: value.update(Name="/vless-agent-xray-2"),
        lambda value: value["NetworkSettings"]["Networks"].update(
            {"vless-agent_management": {"IPAddress": "172.31.255.3"}}
        ),
        lambda value: value["NetworkSettings"]["Networks"].update(
            {"foreign": {"IPAddress": "172.31.255.2"}}
        ),
    ],
)
def test_network_preflight_rejects_reserved_container_inspect_drift(
    mutate: object,
) -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    network = _expected_network()
    container_id = "d" * 64
    network["Containers"] = {
        container_id: {
            "Name": "vless-agent-xray-1",
            "IPv4Address": "172.31.255.2/28",
        }
    }
    container = _preflight_container(
        container_id=container_id, service="xray", address="172.31.255.2/28"
    )
    mutate(container)

    assert not validator([], [], [network], [container], "vless-agent")


@pytest.mark.parametrize(
    ("addresses", "routes", "networks"),
    [
        ({}, [], []),
        ([{"ifname": "eth0", "addr_info": "bad"}], [], []),
        ([], {}, []),
        ([], [{"dst": "malformed", "dev": "eth0"}], []),
        ([], [], {}),
        ([], [], [{"Name": "foreign", "IPAM": {"Config": "bad"}}]),
    ],
)
def test_network_preflight_fails_closed_on_malformed_inspection_data(
    addresses: object, routes: object, networks: object
) -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]

    assert not validator(addresses, routes, networks, [], "vless-agent")


def test_network_preflight_fails_closed_on_missing_or_malformed_container_inspect() -> None:
    validator = _load_role_filters()["vless_agent_network_preflight_safe"]
    network = _expected_network()
    network["Containers"] = {
        "e" * 64: {
            "Name": "vless-agent-xray-1",
            "IPv4Address": "172.31.255.2/28",
        }
    }

    assert not validator([], [], [network], [], "vless-agent")
    assert not validator([], [], [network], [{}], "vless-agent")
    assert not validator([], [], [], [{"Id": "f" * 64}], "vless-agent")


def test_host_and_docker_network_preflight_runs_before_compose_start() -> None:
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    names = [task.get("name") for task in tasks]
    safety = next(task for task in tasks if task.get("name") == "Reject host or Docker management network conflicts")

    assert safety["no_log"] is True
    assert safety["ansible.builtin.assert"]["that"] == [
        "vless_agent_host_ipv4_addresses | vless_agent_network_preflight_safe(vless_agent_host_ipv4_routes, vless_agent_docker_networks, vless_agent_existing_containers, vless_agent_compose_project_name)"
    ]
    assert names.index("Inspect every existing Docker container") < names.index(
        "Reject host or Docker management network conflicts"
    )
    assert names.index("Reject host or Docker management network conflicts") < names.index(
        "Mark candidate Compose as possibly started"
    )


def test_every_compose_command_uses_pinned_project_name_and_volume_relation() -> None:
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    compose_argv = [
        task["ansible.builtin.command"]["argv"]
        for task in tasks
        if isinstance(task.get("ansible.builtin.command"), dict)
        and task["ansible.builtin.command"].get("argv", [])[:2] == ["docker", "compose"]
    ]
    defaults = _yaml(ROLE / "defaults" / "main.yml")
    task_text = _read(ROLE / "tasks" / "main.yml")

    assert len(compose_argv) >= 5
    for argv in compose_argv:
        assert argv[2:4] == ["-p", "{{ vless_agent_compose_project_name }}"]
    assert defaults["vless_agent_compose_project_name"] == "vless-agent"
    assert defaults["vless_agent_snapshot_volume"] == (
        "{{ vless_agent_compose_project_name }}_agent-snapshot"
    )
    assert "vless_agent_compose_project_name is match('^[a-z0-9][a-z0-9_-]*$')" in task_text
    assert "vless_agent_snapshot_volume == vless_agent_compose_project_name ~ '_agent-snapshot'" in task_text


def test_nginx_is_tls_only_ipv4_and_proxies_only_to_internal_agent() -> None:
    nginx = _read(ROLE / "templates" / "vless-agent.nginx.conf.j2")

    assert "listen 0.0.0.0:{{ vless_agent_management_port }} ssl;" in nginx
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in nginx
    assert "proxy_pass http://172.31.255.3:8000;" in nginx
    assert "access_log off;" in nginx
    assert "server_tokens off;" in nginx
    assert "client_max_body_size" in nginx
    assert "listen 80" not in nginx
    assert "[::]" not in nginx
    assert "proxy_pass http://0.0.0.0" not in nginx


def _runtime_inspection(*, agent_ports: object = None) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    network = _expected_network()
    network["Containers"] = {
        "xray-container-id": {
            "Name": "vless-agent-xray-1",
            "IPv4Address": "172.31.255.2/28",
        },
        "agent-container-id": {
            "Name": "vless-agent-agent-1",
            "IPv4Address": "172.31.255.3/28",
        },
    }
    xray = {
        "Id": "xray-container-id-full",
        "Name": "/vless-agent-xray-1",
        "Config": {"Labels": {"com.docker.compose.project": "vless-agent", "com.docker.compose.service": "xray"}},
        "NetworkSettings": {
            "Networks": {
                "vless-agent_management": {"IPAddress": "172.31.255.2"},
                "vless-agent_public": {"IPAddress": "172.20.0.2"},
            }
        },
        "HostConfig": {"PortBindings": {"443/tcp": [{"HostPort": "443"}]}},
    }
    agent = {
        "Id": "agent-container-id-full",
        "Name": "/vless-agent-agent-1",
        "Config": {"Labels": {"com.docker.compose.project": "vless-agent", "com.docker.compose.service": "agent"}},
        "NetworkSettings": {
            "Networks": {"vless-agent_management": {"IPAddress": "172.31.255.3"}},
            "Ports": agent_ports,
        },
        "HostConfig": {
            "PortBindings": {} if agent_ports == {"8000/tcp": None} else agent_ports
        },
    }
    return [network], [xray], [agent]


def test_runtime_topology_accepts_docker_29_normalized_bridge_inspect() -> None:
    validator = _load_role_filters()["vless_agent_valid_runtime_topology"]
    network, xray, agent = _runtime_inspection()
    network[0]["Options"] = {
        "com.docker.network.enable_ipv4": "true",
        "com.docker.network.enable_ipv6": "false",
    }
    network[0]["IPAM"]["Config"][0]["IPRange"] = ""

    assert validator(network, xray, agent, "vless-agent")


@pytest.mark.parametrize("empty_ports", [None, {}, {"8000/tcp": None}])
def test_runtime_topology_validator_accepts_exact_inspection_with_no_agent_bindings(empty_ports: object) -> None:
    validator = _load_role_filters()["vless_agent_valid_runtime_topology"]
    network, xray, agent = _runtime_inspection(agent_ports=empty_ports)

    assert validator(network, xray, agent, "vless-agent")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda network, xray, agent: network[0].update(Internal=False),
        lambda network, xray, agent: network[0].update(Driver="macvlan"),
        lambda network, xray, agent: network[0].update(
            Options={"com.docker.network.bridge.name": "custom"}
        ),
        lambda network, xray, agent: network[0]["IPAM"]["Config"][0].update(Gateway="172.31.255.4"),
        lambda network, xray, agent: xray[0]["NetworkSettings"]["Networks"]["vless-agent_management"].update(IPAddress="172.31.255.4"),
        lambda network, xray, agent: xray[0]["NetworkSettings"]["Networks"].pop("vless-agent_public"),
        lambda network, xray, agent: agent[0]["NetworkSettings"]["Networks"]["vless-agent_management"].update(IPAddress="172.31.255.4"),
        lambda network, xray, agent: agent[0]["NetworkSettings"]["Networks"].update(**{"vless-agent_public": {"IPAddress": "172.20.0.3"}}),
        lambda network, xray, agent: agent[0]["NetworkSettings"].update(Ports={"8000/tcp": [{"HostPort": "8000"}]}),
        lambda network, xray, agent: agent[0]["HostConfig"].update(PortBindings={"8000/tcp": [{"HostPort": "8000"}]}),
        lambda network, xray, agent: agent[0]["Config"]["Labels"].update({"com.docker.compose.project": "other"}),
    ],
)
def test_runtime_topology_validator_rejects_drift_and_agent_publication(mutate: object) -> None:
    validator = _load_role_filters()["vless_agent_valid_runtime_topology"]
    network, xray, agent = _runtime_inspection()
    mutate(network, xray, agent)

    assert not validator(network, xray, agent, "vless-agent")


def test_runtime_inspection_and_direct_health_precede_nginx_install() -> None:
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    names = [task.get("name") for task in tasks]
    direct = next(task for task in tasks if task.get("name") == "Verify authenticated direct bridge health")

    assert direct["ansible.builtin.uri"]["url"] == "http://172.31.255.3:8000/api/v1/health"
    assert direct["ansible.builtin.uri"]["use_proxy"] is False
    assert direct["ansible.builtin.uri"]["headers"] == {
        "Authorization": "Bearer {{ vless_agent_token_current }}",
        "X-Agent-Contract-Version": "v1",
    }
    assert direct["no_log"] is True
    assert names.index("Require exact running management topology") < names.index(
        "Verify authenticated direct bridge health"
    )
    assert names.index("Verify authenticated direct bridge health") < names.index(
        "Install nginx TLS virtual host"
    )
    for suffix in (
        "status == 200",
        "json.agent_sha == vless_agent_active_revision",
        "json.contract_version == 'v1'",
        "json.schema_version == '1.0'",
        "json.xray_version == '26.7.11'",
        f"json.xray_image_digest == '{XRAY_DIGEST}'",
        "json.readiness in ['READY', 'RECOVERY_READY', 'NOT_READY']",
    ):
        assert "vless_agent_direct_health." + suffix in direct["until"]


def test_tls_preflight_checks_files_expiry_hostname_and_key_match() -> None:
    tasks = _read(ROLE / "tasks" / "main.yml")

    assert "vless_agent_tls_cert_stat.stat.exists" in tasks
    assert "vless_agent_tls_key_stat.stat.mode in ['0400', '0600']" in tasks
    assert "vless_agent_tls_key_stat.stat.pw_name == 'root'" in tasks
    assert "vless_agent_tls_cert_stat.stat.pw_name == 'root'" in tasks
    assert "vless_agent_tls_cert_stat.stat.mode is match('^0[0-7][0145][0145]$')" in tasks
    assert "openssl x509 -checkend" in tasks
    assert "openssl x509 -checkhost" in tasks
    assert "Certificate and private key must match" in tasks
    assert "no_log: true" in tasks


def test_firewall_owns_only_management_chain_and_is_reversible() -> None:
    script = _read(ROLE / "templates" / "vless-agent-firewall.sh.j2")
    unit = _read(ROLE / "templates" / "vless-agent-firewall.service.j2")
    tasks = _read(ROLE / "tasks" / "main.yml")

    assert "VLESS_AGENT_MGMT" in script
    assert "VLESS_AGENT_MGMT_NEW" in script
    assert '--dport "$PORT"' in script
    assert "central_backend_ipv4_allowlist" in script
    assert "-j REJECT" in script
    assert "stop)" in script and "-D INPUT" in script and "-X \"$target\"" in script
    assert 'remove_chain "$CHAIN"' in script
    assert "iptables -F\n" not in script
    assert "--flush" not in script
    assert "--dport 443" not in script
    assert "--dport 22" not in script
    assert "ExecStop=" in unit
    assert "ExecReload=" in unit
    assert "Reload management firewall atomically" in tasks
    for operation in re.finditer(r"\biptables\b", script):
        assert script[operation.end() :].lstrip().startswith("-w ")
    assert tasks.index("Enable management firewall") < tasks.index("Install nginx TLS virtual host")


def test_rendered_firewall_allows_only_loopback_interface_then_backend_sources() -> None:
    rendered = _render_firewall(sources=["192.0.2.10", "198.51.100.7"])
    rules = [
        line.strip()
        for line in rendered.splitlines()
        if 'iptables -w -A "$NEXT_CHAIN"' in line
    ]

    assert rules[0] == (
        'iptables -w -A "$NEXT_CHAIN" -i lo -p tcp --dport "$PORT" '
        "-j ACCEPT # local_health_only"
    )
    assert "-s 192.0.2.10" in rules[1] and "-j ACCEPT" in rules[1]
    assert "-s 198.51.100.7" in rules[2] and "-j ACCEPT" in rules[2]
    assert rules[3].endswith('-p tcp --dport "$PORT" -j REJECT')
    assert "-s 127." not in rendered
    external_accepts = [rule for rule in rules if "-j ACCEPT" in rule and "-i lo" not in rule]
    assert all("-s " in rule for rule in external_accepts)


def test_firewall_lifecycle_removes_old_port_jumps_without_fail_open(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_iptables(bin_dir / "iptables")
    state_path = tmp_path / "iptables.json"
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_IPTABLES_STATE": str(state_path)}

    scripts: list[Path] = []
    for port in (8443, 9443):
        script = tmp_path / f"firewall-{port}"
        script.write_text(_render_firewall(sources=["192.0.2.10"], port=port), encoding="utf-8")
        script.chmod(0o700)
        scripts.append(script)

    subprocess.run([scripts[0], "start"], check=True, env=environment)
    subprocess.run([scripts[1], "start"], check=True, env=environment)
    reloaded = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(reloaded["chains"]) == {"INPUT", "VLESS_AGENT_MGMT"}
    assert len(reloaded["chains"]["INPUT"]) == 1
    assert reloaded["chains"]["INPUT"][0] == [
        "-p", "tcp", "--dport", "9443", "-j", "VLESS_AGENT_MGMT"
    ]

    subprocess.run([scripts[1], "stop"], check=True, env=environment)
    stopped = json.loads(state_path.read_text(encoding="utf-8"))
    assert stopped == {"chains": {"INPUT": []}}


def test_management_port_state_blocks_uncoordinated_change_before_mutation() -> None:
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    names = [task.get("name") for task in tasks]
    guard = next(task for task in tasks if task.get("name") == "Reject uncoordinated management port change")
    persist = next(task for task in tasks if task.get("name") == "Persist successful management port")

    assert guard["no_log"] is True
    assert any(
        "vless_persisted_management_port == (vless_agent_management_port | string)" in condition
        for condition in guard["ansible.builtin.assert"]["that"]
    )
    assert "coordinated_management_port_migration" not in _read(
        ROLE / "defaults" / "main.yml"
    )
    assert "coordinated_management_port_migration" not in _read(
        ROLE / "tasks" / "main.yml"
    )
    assert guard["ansible.builtin.assert"]["that"][-1] == (
        "vless_persisted_management_port == (vless_agent_management_port | string)"
    )
    assert names.index("Reject uncoordinated management port change") < names.index("Install management firewall script")
    assert names.index("Reject uncoordinated management port change") < names.index("Install nginx TLS virtual host")
    assert names.index("Reject uncoordinated management port change") < names.index(
        "Install explicit runtime packages without auto-starting nginx"
    )
    assert persist["ansible.builtin.copy"]["mode"] == "0600"
    assert names.index("Verify authenticated HTTPS health") < names.index("Persist successful management port")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("192.0.2.10", True),
        ("198.51.100.7", True),
        ("10.0.0.1", True),
        ("999.0.0.1", False),
        ("192.0.2.0/24", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("255.255.255.255", False),
        ("2001:db8::1", False),
    ],
)
def test_firewall_source_validator_accepts_only_explicit_unicast_ipv4(
    value: str, expected: bool
) -> None:
    validator = _load_role_filters()["vless_agent_unicast_ipv4"]

    assert validator(value) is expected


def test_firewall_allowlist_task_uses_behavioral_ipv4_filter() -> None:
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    validation_task = next(
        task
        for task in tasks
        if task.get("name") == "Validate every central backend IPv4 source"
    )

    assert validation_task["loop"] == "{{ vless_agent_central_backend_ipv4_allowlist }}"
    assert validation_task["ansible.builtin.assert"]["that"] == [
        "item | vless_agent_unicast_ipv4"
    ]


def test_nginx_first_install_starts_only_after_validated_tls_configuration() -> None:
    tasks = _read(ROLE / "tasks" / "main.yml")

    assert "policy_rc_d: 101" in tasks
    assert tasks.index("Validate nginx configuration") < tasks.index(
        "Start nginx after firewall is active"
    )
    assert "state: started" in tasks[tasks.index("Start nginx after firewall is active") :]


def test_effective_nginx_listeners_are_host_wide_tls_only_before_reload() -> None:
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    names = [task.get("name") for task in tasks]
    dump = next(task for task in tasks if task.get("name") == "Inspect effective nginx configuration")
    assertion = next(task for task in tasks if task.get("name") == "Require exactly one effective HTTPS listener")

    assert dump["ansible.builtin.command"]["argv"] == ["nginx", "-T"]
    assert dump["changed_when"] is False and dump["no_log"] is True
    assert assertion["no_log"] is True
    assert assertion["ansible.builtin.assert"]["that"] == [
        "vless_agent_effective_nginx_listeners == ['0.0.0.0:' ~ (vless_agent_management_port | string) ~ ' ssl']"
    ]
    assert names.index("Install nginx TLS virtual host") < names.index("Inspect effective nginx configuration")
    assert names.index("Require exactly one effective HTTPS listener") < names.index("Start nginx after firewall is active")
    assert names.index("Disable packaged nginx default listener symlink") < names.index(
        "Install nginx TLS virtual host"
    )


def test_packaged_nginx_default_is_removed_only_when_known_safe_symlink() -> None:
    validator = _load_role_filters()["vless_agent_safe_packaged_nginx_default"]
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    by_name = {task.get("name"): task for task in tasks}

    assert validator({"exists": False})
    assert validator(
        {
            "exists": True,
            "islnk": True,
            "lnk_source": "/etc/nginx/sites-available/default",
        }
    )
    assert not validator(
        {"exists": True, "islnk": False, "isreg": True, "path": "operator-file"}
    )
    assert not validator(
        {"exists": True, "islnk": True, "lnk_source": "/srv/operator-nginx.conf"}
    )

    inspect = by_name["Inspect packaged nginx default listener"]
    safety = by_name["Require packaged nginx default listener safety"]
    disable = by_name["Disable packaged nginx default listener symlink"]
    assert inspect["ansible.builtin.stat"] == {
        "path": "/etc/nginx/sites-enabled/default",
        "follow": False,
    }
    assert safety["ansible.builtin.assert"]["that"] == [
        "vless_packaged_nginx_default.stat | vless_agent_safe_packaged_nginx_default"
    ]
    assert disable["ansible.builtin.file"] == {
        "path": "/etc/nginx/sites-enabled/default",
        "state": "absent",
    }
    assert disable["when"] == "vless_packaged_nginx_default.stat.exists"


def test_nginx_listener_parser_ignores_comments_and_rejects_extra_listeners() -> None:
    parser = _load_role_filters()["vless_agent_nginx_listeners"]

    assert parser("# listen 80;\n listen 0.0.0.0:8443 ssl; # managed\n") == [
        "0.0.0.0:8443 ssl"
    ]
    assert parser("listen 0.0.0.0:8443 ssl;\nlisten [::]:8443 ssl;\n") != [
        "0.0.0.0:8443 ssl"
    ]
    assert parser("listen 0.0.0.0:8443 ssl;\nlisten\n  80;\n") != [
        "0.0.0.0:8443 ssl"
    ]
    inline = """
    # server { listen 81; }
    log_format decoy "server { listen 82; }";
    set $quoted 'listen 83;';
    server {\tlisten 80; listen\n  0.0.0.0:8443\tssl; }
    """
    assert parser(inline) == ["80", "0.0.0.0:8443 ssl"]


@pytest.mark.parametrize(
    ("configuration", "expected"),
    [
        (
            "server {\n    listen 0.0.0.0:8443 ssl;\n}",
            ["0.0.0.0:8443 ssl"],
        ),
        ("server { listen 0.0.0.0:8443 ssl; }", ["0.0.0.0:8443 ssl"]),
        ("server {\tlisten\t0.0.0.0:8443\tssl; }", ["0.0.0.0:8443 ssl"]),
        (
            "server {\n    listen\n        0.0.0.0:8443\n        ssl;\n}",
            ["0.0.0.0:8443 ssl"],
        ),
        (
            "server {\n"
            "    listen 0.0.0.0:8443 ssl;\n"
            "    listen 0.0.0.0:8443 ssl;\n"
            "}",
            ["0.0.0.0:8443 ssl", "0.0.0.0:8443 ssl"],
        ),
    ],
)
def test_nginx_listener_parser_returns_each_source_directive_once(
    configuration: str, expected: list[str]
) -> None:
    parser = _load_role_filters()["vless_agent_nginx_listeners"]

    assert parser(configuration) == expected


def test_previous_compose_state_requires_running_agent_and_xray() -> None:
    validator = _load_role_filters()["vless_agent_compose_runtime_was_running"]

    assert validator(json.dumps([
        {"Service": "agent", "State": "running"},
        {"Service": "xray", "State": "running"},
        {"Service": "xray-config", "State": "exited"},
    ]))
    assert not validator(json.dumps([
        {"Service": "agent", "State": "running"},
        {"Service": "xray", "State": "exited"},
    ]))
    assert not validator("not-json")


def test_health_gate_is_verified_authenticated_exact_and_bounded() -> None:
    tasks = _read(ROLE / "tasks" / "main.yml")

    assert "https://{{ vless_agent_domain }}:{{ vless_agent_management_port }}/api/v1/health" in tasks
    assert "validate_certs: true" in tasks
    assert "Authorization: Bearer {{ vless_agent_token_current }}" in tasks
    assert "X-Agent-Contract-Version: v1" in tasks
    assert "vless_agent_health.json.agent_sha == vless_agent_active_revision" in tasks
    assert "vless_agent_health.json.xray_version == '26.7.11'" in tasks
    assert "vless_agent_health.json.schema_version == '1.0'" in tasks
    assert f"vless_agent_health.json.xray_image_digest == '{XRAY_DIGEST}'" in tasks
    assert "retries: 12" in tasks and "delay: 5" in tasks
    assert "no_log: true" in tasks
    assert "validate_certs: false" not in tasks
    assert "curl -k" not in tasks


def test_deploy_and_rollback_health_requests_bypass_host_proxy() -> None:
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    health_requests = [
        task["ansible.builtin.uri"]
        for task in tasks
        if "ansible.builtin.uri" in task
        and task["ansible.builtin.uri"]["url"].endswith("/api/v1/health")
    ]

    assert len(health_requests) == 3
    assert all(request["use_proxy"] is False for request in health_requests)


def test_compose_runtime_preflight_runs_before_checkout_without_mutation() -> None:
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    preflight = next(
        task for task in tasks if task.get("name") == "Preflight Docker Compose runtime"
    )
    task_names = [task.get("name") for task in tasks]

    assert preflight["ansible.builtin.command"]["argv"] == [
        "docker",
        "compose",
        "-p",
        "{{ vless_agent_compose_project_name }}",
        "version",
    ]
    assert preflight["changed_when"] is False
    assert task_names.index("Enable Docker") < task_names.index(
        "Preflight Docker Compose runtime"
    )
    assert task_names.index("Preflight Docker Compose runtime") < task_names.index(
        "Inspect existing checkout"
    )


def test_health_does_not_expect_non_contract_node_id() -> None:
    tasks = _read(ROLE / "tasks" / "main.yml")

    assert "vless_agent_health.json.node_id" not in tasks
    assert "vless_agent_rollback_health.json.node_id" not in tasks
    assert "vault_vless_agent_tokens[inventory_hostname]" in tasks
    assert "https://{{ vless_agent_domain }}" in tasks
    assert "vless_agent_checked_out_revision.stdout != deploy_revision" in tasks


def test_snapshot_backup_is_private_bounded_and_volume_is_preserved() -> None:
    tasks = _read(ROLE / "tasks" / "main.yml")
    all_text = _all_deploy_text()

    assert "Back up current snapshot privately" in tasks
    assert 'mode: "0600"' in tasks
    assert "vless_agent_snapshot_backup_retention" in tasks
    assert "age_stamp" in tasks
    assert "agent-snapshot" in tasks
    assert "no_log: true" in tasks
    assert "down -v" not in all_text
    assert "docker volume rm" not in all_text
    assert "volume prune" not in all_text


def test_failed_deploy_rolls_back_only_to_declared_compatible_exact_sha() -> None:
    tasks = _yaml(ROLE / "tasks" / "main.yml")
    text = _read(ROLE / "tasks" / "main.yml")

    assert any("block" in task and "rescue" in task for task in tasks)
    assert "vless_previous_revision is match('^[0-9a-f]{40}$')" in text
    assert "vless_previous_revision in vless_agent_compatible_rollback_revisions" in text
    assert "No automatic rollback is possible on a first install" in text
    assert "Verify rolled back HTTPS health" in text
    assert "docker compose down" not in text


def test_rollback_captures_and_restores_prior_configuration_without_current_vars() -> None:
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    names = [task.get("name") for task in tasks]
    task_by_name = {task.get("name"): task for task in tasks}

    for capture, overwrite in (
        ("Capture previous private agent environment", "Render private agent environment"),
        ("Capture previous REALITY key", "Render node REALITY key file"),
        ("Capture previous nginx virtual host", "Install nginx TLS virtual host"),
    ):
        assert names.index(capture) < names.index(overwrite)
        assert task_by_name[capture]["no_log"] is True

    can_rollback = _read(ROLE / "tasks" / "main.yml")
    assert "vless_previous_config_artifacts_available" in can_rollback
    assert "vless_previous_compose_was_running" in can_rollback
    assert task_by_name["Restore previous private agent environment"]["ansible.builtin.copy"]["content"] == (
        "{{ vless_previous_agent_env.content | b64decode }}"
    )
    assert task_by_name["Restore previous REALITY key"]["ansible.builtin.copy"]["content"] == (
        "{{ vless_previous_reality_key.content | b64decode }}"
    )
    assert task_by_name["Restore previous nginx virtual host"]["ansible.builtin.copy"]["content"] == (
        "{{ vless_previous_nginx_config.content | b64decode }}"
    )
    assert task_by_name["Restore previous private agent environment"]["no_log"] is True
    assert "src" not in task_by_name["Restore previous private agent environment"]["ansible.builtin.copy"]
    assert names.index("Validate restored nginx configuration") < names.index(
        "Restart compatible previous revision using the same snapshot volume"
    )
    assert names.index("Reload restored nginx configuration") < names.index(
        "Restart compatible previous revision using the same snapshot volume"
    )
    assert task_by_name["Remove candidate nginx configuration without prior artifact"]["ansible.builtin.file"]["state"] == "absent"
    assert names.index("Remove candidate nginx configuration without prior artifact") < names.index(
        "Explain first-install or incompatible rollback refusal"
    )
    rollback_health = task_by_name["Verify rolled back HTTPS health"]["ansible.builtin.uri"]
    assert rollback_health["url"] == (
        "https://{{ vless_previous_agent_domain }}:{{ vless_previous_management_port }}/api/v1/health"
    )
    assert rollback_health["headers"]["Authorization"] == (
        "Bearer {{ vless_previous_agent_token_current }}"
    )


def test_no_compatible_rollback_cleanup_is_flagged_fail_closed_and_volume_safe() -> None:
    tasks = _walk_tasks(_yaml(ROLE / "tasks" / "main.yml"))
    names = [task.get("name") for task in tasks]
    by_name = {task.get("name"): task for task in tasks}

    initialize = by_name["Initialize candidate mutation flags"]
    assert initialize["ansible.builtin.set_fact"] == {
        "vless_candidate_compose_may_have_started": False,
        "vless_candidate_nginx_may_have_loaded": False,
    }
    assert names.index("Initialize candidate mutation flags") < names.index(
        "Render node REALITY key file"
    )
    assert names.index("Mark candidate Compose as possibly started") + 1 == names.index(
        "Replace services while preserving named snapshot volume"
    )
    assert names.index("Mark candidate nginx as possibly loaded") + 1 == names.index(
        "Start nginx after firewall is active"
    )

    stop_compose = by_name["Stop candidate Compose project containers without deleting volumes"]
    stop_nginx = by_name["Stop nginx when candidate configuration may have loaded"]
    assert stop_compose["when"] == [
        "not vless_agent_can_rollback",
        "vless_candidate_compose_may_have_started",
    ]
    assert stop_compose["no_log"] is True
    cleanup_script = stop_compose["ansible.builtin.shell"]
    assert "label=com.docker.compose.project={{ vless_agent_compose_project_name }}" in cleanup_script
    assert "docker stop" in cleanup_script
    assert "docker compose" not in cleanup_script
    assert stop_nginx["when"] == [
        "not vless_agent_can_rollback",
        "vless_candidate_nginx_may_have_loaded",
    ]
    assert stop_nginx["ansible.builtin.service"]["state"] == "stopped"

    for name, previous_stat in (
        ("Restore captured agent environment without compatible rollback", "vless_previous_agent_env_stat.exists"),
        ("Restore captured REALITY key without compatible rollback", "vless_previous_reality_key_stat.exists"),
        ("Restore captured nginx configuration without compatible rollback", "vless_previous_nginx_stat.exists"),
    ):
        task = by_name[name]
        assert task["when"] == ["not vless_agent_can_rollback", previous_stat]
        assert task["no_log"] is True

    remove_nginx = by_name["Remove candidate nginx configuration without prior artifact"]
    assert remove_nginx["when"] == [
        "not vless_agent_can_rollback",
        "not vless_previous_nginx_stat.exists",
    ]
    failure_index = names.index("Explain first-install or incompatible rollback refusal")
    assert names.index("Stop candidate Compose project containers without deleting volumes") < failure_index
    assert names.index("Stop nginx when candidate configuration may have loaded") < failure_index
    assert names.index("Restore captured agent environment without compatible rollback") < failure_index

    role_text = _read(ROLE / "tasks" / "main.yml")
    assert "down -v" not in role_text
    assert "docker volume rm" not in role_text
    assert "volume prune" not in role_text


def test_documentation_covers_safe_rollout_recovery_rotation_and_approval() -> None:
    deploy_doc = _read(ROOT / "docs" / "DEPLOY.md").lower()
    compatibility = _read(ROOT / "docs" / "COMPATIBILITY.md")
    normalized_compatibility = " ".join(compatibility.lower().split())

    for phrase in (
        "test first",
        "serial",
        "ansible vault",
        "tls",
        "firewall",
        "backup",
        "restore drill",
        "current + next",
        "backend switch",
        "promote",
        "rollback",
        "explicit user approval",
    ):
        assert phrase in deploy_doc
    assert "contract v1" in compatibility
    assert "snapshot schema 1.0" in compatibility
    assert "26.7.11" in compatibility
    assert XRAY_DIGEST in compatibility
    assert REVIEWED_AGENT_SHA in compatibility
    assert "upgrade" in compatibility.lower() and "downgrade" in compatibility.lower()
    assert "a-007 runtime baseline" in normalized_compatibility
    assert "not a later infrastructure or ci revision" in normalized_compatibility
    assert (
        "intentionally does not claim a-010 has been test-deployed"
        in normalized_compatibility
    )
    assert "does not yet add its candidate sha" in normalized_compatibility


def test_operational_docs_define_direct_bridge_bootstrap_boundary() -> None:
    docs = {
        path: " ".join(_read(ROOT / path).lower().split())
        for path in (
            "README.md",
            "docs/DEPLOY.md",
            "docs/SECURITY.md",
            "docs/RUNBOOK.md",
            "docs/COMPATIBILITY.md",
        )
    }
    combined = "\n".join(docs.values())

    assert "tls is mandatory" in combined
    assert "sole plaintext exception" in combined
    assert MANAGEMENT_SUBNET in combined
    for address in ("172.31.255.1", "172.31.255.2", "172.31.255.3"):
        assert address in combined
    assert "fail closed" in combined or "fails closed" in combined
    assert "overlap" in combined and "drift" in combined
    assert "not an operational rollback target" in docs["docs/COMPATIBILITY.md"]
    assert REVIEWED_AGENT_SHA in docs["docs/COMPATIBILITY.md"]
    assert "bootstrap evidence" in combined and "external" in combined
    assert "separate final tracked commit" in combined
    assert "no bootstrap sha" in combined

    for example in (
        "deploy/group_vars/vless_test.example.yml",
        "deploy/group_vars/vless_prod.example.yml",
    ):
        rollback_revisions = _yaml(ROOT / example)[
            "vless_agent_compatible_rollback_revisions"
        ]
        assert REVIEWED_AGENT_SHA not in rollback_revisions


def test_loopback_baseline_is_rejected_as_bootstrap_rollback_target() -> None:
    tasks = _read(ROLE / "tasks" / "main.yml")

    assert (
        f"'{REVIEWED_AGENT_SHA}' not in vless_agent_compatible_rollback_revisions"
        in tasks
    )


def test_deploy_docs_describe_contract_accurate_identity_anchor() -> None:
    deploy_doc = _read(ROOT / "docs" / "DEPLOY.md").lower()

    assert "inventory-specific token" in deploy_doc
    assert "certificate/domain" in deploy_doc
    assert "canonical health has no `node_id`" in deploy_doc
    assert "health gate verifies tls, bearer authentication, contract v1, node identity" not in deploy_doc
    assert "loopback-interface exception" in deploy_doc
    assert "host proxy is disabled" in deploy_doc


def test_deploy_docs_cover_port_migration_hostwide_nginx_and_exact_rollback_scope() -> None:
    deploy_doc = " ".join(_read(ROOT / "docs" / "DEPLOY.md").lower().split())

    assert "separate reviewed implementation" in deploy_doc
    assert "no variable override" in deploy_doc
    assert "migration-from" not in deploy_doc
    assert "persisted root-only port state" in deploy_doc
    assert "nginx -t" in deploy_doc
    assert "every effective `listen`" in deploy_doc
    assert "prior environment, reality key, and nginx" in deploy_doc
    assert "previous compose project was running" in deploy_doc
    assert "first install removes the candidate nginx" in deploy_doc
    assert "packaged enabled symlink" in deploy_doc
    assert "project-labeled candidate containers" in deploy_doc
    assert "without deleting volumes" in deploy_doc
    assert "early failure before either candidate flag" in deploy_doc
    assert "does not stop the healthy prior runtime or nginx" in deploy_doc
    assert "never checks out or starts an undeclared previous sha" in deploy_doc


def test_deploy_artifacts_contain_no_unsafe_secret_or_mutable_runtime() -> None:
    text = _all_deploy_text()

    forbidden = (
        "BEGIN PRIVATE KEY",
        "BEGIN CERTIFICATE",
        "validate_certs: false",
        "curl | sh",
        "curl -k",
        "http://{{ vless_agent_domain }}",
        "xray-core:latest",
        "version: main",
        "version: master",
        "version: HEAD",
    )
    for value in forbidden:
        assert value not in text
    private_addresses = set(
        re.findall(
            r"(?<![\d.])(10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)",
            text,
        )
    )
    assert private_addresses <= {"172.31.255.0", "172.31.255.1", "172.31.255.2", "172.31.255.3"}
