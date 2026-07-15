from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator

import pytest

from src.api.schemas import AccessDTO
from src.app import create_app
from src.config import EnvironmentMode, Settings
from src.xray import XrayUser, access_email, create_apply_exact_set_service


XRAY_VERSION = "26.7.11"
XRAY_IMAGE = (
    "teddysun/xray:26.7.11@"
    "sha256:1a733f97199801d00015cd5ecd2265c6af5dcf42c9282670e131a6b152840802"
)
MANAGED_TAG = "vless-managed"
UNMANAGED_TAG = "vless-unmanaged"


def _free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _xray_config() -> dict[str, object]:
    return {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "services": ["HandlerService"]},
        "inbounds": [
            {
                "tag": "api-in",
                "listen": "0.0.0.0",
                "port": 10085,
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
            },
            {
                "tag": MANAGED_TAG,
                "listen": "127.0.0.1",
                "port": 11001,
                "protocol": "vless",
                "settings": {"clients": [], "decryption": "none"},
            },
            {
                "tag": UNMANAGED_TAG,
                "listen": "127.0.0.1",
                "port": 11002,
                "protocol": "vless",
                "settings": {
                    "clients": [
                        {
                            "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                            "email": "operator-owned@example.invalid",
                        }
                    ],
                    "decryption": "none",
                },
            },
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["api-in"],
                    "outboundTag": "api",
                }
            ]
        },
    }


@pytest.fixture(scope="module")
def real_xray(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable; real-Xray integration cannot run")
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe.returncode != 0:
        pytest.skip("Docker daemon is unavailable; real-Xray integration cannot run")

    api_port = _free_port()
    config_dir = tmp_path_factory.mktemp("xray")
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps(_xray_config()), encoding="utf-8")
    container_name = f"vless-agent-xray-test-{api_port}"
    command = [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--publish",
        f"127.0.0.1:{api_port}:10085",
        "--volume",
        f"{config_path}:/etc/xray/config.json:ro",
        XRAY_IMAGE,
    ]
    started = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if started.returncode != 0:
        pytest.fail(f"pinned Xray container failed to start: {started.stderr.strip()}")

    target = f"127.0.0.1:{api_port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with socket.socket() as client:
                client.settimeout(0.25)
                if client.connect_ex(("127.0.0.1", api_port)) == 0:
                    break
            time.sleep(0.1)
        else:
            logs = subprocess.run(
                ["docker", "logs", container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            pytest.fail(f"pinned Xray API did not become ready: {logs.stderr}{logs.stdout}")
        version = subprocess.run(
            ["docker", "exec", container_name, "/usr/bin/xray", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if version.returncode != 0 or f"Xray {XRAY_VERSION}" not in version.stdout:
            pytest.fail("pinned container did not report the required Xray version")
        yield target
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container_name],
            capture_output=True,
            timeout=10,
        )


def _access(access_id: int, uuid: str, *, revision: int = 1) -> AccessDTO:
    return AccessDTO(access_id=access_id, uuid=uuid, access_revision=revision)


def test_real_xray_reconciles_exact_set_and_preserves_unmanaged_inbound(
    real_xray: str,
) -> None:
    service = create_apply_exact_set_service(
        target=real_xray,
        managed_inbound_tag=MANAGED_TAG,
        timeout_seconds=3,
    )
    first = _access(2, "01890f47-a2d4-7c11-b3e6-89f40d8639f1")
    second = _access(10, "2f1c5a63-7bd6-4ac1-86dc-16b7adf580df")

    service(accesses=(first, second))
    service(accesses=(first, second))
    service(accesses=(_access(2, second.uuid, revision=2),))

    assert service.client.get_inbound_users(tag=MANAGED_TAG) == (
        XrayUser(email=access_email(2), uuid=second.uuid),
    )
    assert service.client.get_inbound_users(tag=UNMANAGED_TAG) == (
        XrayUser(
            email="operator-owned@example.invalid",
            uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        ),
    )


def test_public_routes_contain_no_incremental_mutation_endpoint() -> None:
    app = create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            node_api_token_current="a" * 32,
        )
    )

    paths = {route.path.lower() for route in app.routes}

    assert all("add" not in path for path in paths)
    assert all("remove" not in path for path in paths)
    assert all("delete" not in path for path in paths)
    assert all("user" not in path for path in paths)
