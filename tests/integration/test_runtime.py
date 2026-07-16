from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.api.schemas import SnapshotDTO
from src.factories import InitializeRuntimeService
from src.observability import Observability
from src.services import AgentRuntimeState, Readiness, StartupRestoreService
from src.storage import SnapshotStore
from src.xray.config_renderer import (
    PINNED_XRAY_DIGEST,
    PINNED_XRAY_IMAGE,
    PINNED_XRAY_VERSION,
    RuntimeConfigError,
    render_xray_config,
)


ROOT = Path(__file__).parents[2]
PRIVATE_KEY = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA"


def _compose(name: str = "docker-compose.yml") -> dict[str, Any]:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        "REALITY_PRIVATE_KEY": PRIVATE_KEY,
        "REALITY_TARGET": "origin.example:443",
        "REALITY_SERVER_NAME": "www.example.com",
        "REALITY_SHORT_IDS": "0123456789abcdef,deadbeef",
        "VLESS_PUBLIC_PORT": "443",
        "XRAY_MANAGEMENT_IP": "172.31.255.2",
    }
    values.update(overrides)
    return values


def _public_resolution(*_: object) -> list[tuple[object, ...]]:
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def _tls_13(*_: object, **__: object) -> str:
    return "TLSv1.3"


def _render(tmp_path: Path, **overrides: str) -> dict[str, Any]:
    output = tmp_path / "config.json"
    return render_xray_config(
        environment=_environment(**overrides),
        template_path=ROOT / "xray/config.template.json",
        output_path=output,
        resolver=_public_resolution,
        tls_connector=_tls_13,
    )


def test_production_compose_pins_official_xray_and_has_no_mutable_selector() -> None:
    compose = _compose()
    xray = compose["services"]["xray"]

    assert xray["image"] == PINNED_XRAY_IMAGE
    assert PINNED_XRAY_VERSION == "26.7.11"
    assert PINNED_XRAY_DIGEST == (
        "sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f"
    )
    assert "${" not in xray["image"]
    assert "${XRAY_IMAGE" not in json.dumps(compose)


def test_compose_model_never_contains_reality_private_key(tmp_path: Path) -> None:
    secret = tmp_path / "reality_private_key"
    secret.write_text(PRIVATE_KEY, encoding="ascii")
    secret.chmod(0o600)
    environment = {
        **os.environ,
        "AGENT_TOKEN_CURRENT": "verification-only-token",
        "AGENT_SHA": "a" * 40,
        "REALITY_PRIVATE_KEY": PRIVATE_KEY,
        "REALITY_PRIVATE_KEY_FILE": str(secret),
    }

    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "config", "--format", "json"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert PRIVATE_KEY not in result.stdout
    model = json.loads(result.stdout)
    renderer = model["services"]["xray-config"]
    assert "REALITY_PRIVATE_KEY" not in renderer["environment"]
    assert renderer["environment"]["REALITY_PRIVATE_KEY_FILE"] == "/run/secrets/reality_private_key"


def test_compose_orders_init_xray_agent_and_isolates_management_api() -> None:
    compose = _compose()
    services = compose["services"]

    assert services["xray-config"]["depends_on"]["runtime-volume-init"]["condition"] == "service_completed_successfully"
    assert services["runtime-volume-init"]["restart"] == "no"
    assert services["xray-config"]["restart"] == "no"
    assert services["xray"]["depends_on"]["xray-config"]["condition"] == "service_completed_successfully"
    assert services["agent"]["depends_on"]["xray"]["condition"] == "service_healthy"
    assert services["xray"]["healthcheck"]["test"][0] == "CMD"
    assert services["agent"]["healthcheck"]["test"][0] == "CMD"
    assert "ports" not in services["agent"]
    assert services["xray"]["ports"] == ["${VLESS_PUBLIC_PORT:-443}:${VLESS_PUBLIC_PORT:-443}/tcp"]
    assert {
        service_name
        for service_name, service in services.items()
        if "ports" in service
    } == {"xray"}
    assert "10085" not in json.dumps(services["xray"].get("ports", []))
    assert compose["networks"]["management"] == {
        "internal": True,
        "ipam": {
            "config": [
                {
                    "subnet": "172.31.255.0/28",
                    "gateway": "172.31.255.1",
                }
            ]
        },
    }
    assert services["agent"]["networks"] == {
        "management": {"ipv4_address": "172.31.255.3"}
    }
    assert set(services["xray"]["networks"]) == {"management", "public"}
    assert services["xray"]["networks"]["management"] == {
        "ipv4_address": "172.31.255.2"
    }
    assert "3x-ui" not in json.dumps(compose).lower()


@pytest.mark.parametrize("service", ("xray-config", "xray", "agent"))
def test_runtime_containers_are_hardened(service: str) -> None:
    config = _compose()["services"][service]

    assert config["read_only"] is True
    assert config["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in config["security_opt"]
    assert str(config["user"]).split(":", 1)[0] not in {"0", "root"}
    if service == "xray":
        assert any(volume.endswith(":ro") for volume in config["volumes"])


def test_runtime_integration_uses_no_mutable_helper_image() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    assert "busy" + "box" not in source


def test_fresh_compose_snapshot_volume_is_writable_by_agent_uid(tmp_path: Path) -> None:
    if __import__("shutil").which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe.returncode != 0:
        pytest.skip("Docker daemon is unavailable")

    secret = tmp_path / "reality-private-key"
    secret.write_text(PRIVATE_KEY, encoding="ascii")
    secret.chmod(0o600)
    project = f"a007snapshot{uuid.uuid4().hex[:10]}"
    environment = {
        **os.environ,
        "AGENT_TOKEN_CURRENT": "verification-only-token",
        "AGENT_SHA": "a" * 40,
        "REALITY_PRIVATE_KEY_FILE": str(secret),
    }
    compose = ["docker", "compose", "-p", project, "-f", "docker-compose.yml"]
    try:
        initialized = subprocess.run(
            [*compose, "up", "--build", "--abort-on-container-exit", "runtime-volume-init"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert initialized.returncode == 0, initialized.stderr
        write = subprocess.run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "--build",
                "--user",
                "999:999",
                "--entrypoint",
                "/app/.venv/bin/python",
                "agent",
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('/var/lib/vless-agent/write-probe').write_text('probe', encoding='ascii')"
                ),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert write.returncode == 0, write.stderr
    finally:
        subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )


def test_rendered_config_has_exact_managed_reality_and_private_api(tmp_path: Path) -> None:
    config = _render(tmp_path)
    vless = [item for item in config["inbounds"] if item["protocol"] == "vless"]
    api = [item for item in config["inbounds"] if item["tag"] == "api-in"]

    assert len(vless) == 1
    assert vless[0]["tag"] == "vless-managed"
    assert vless[0]["settings"] == {"clients": [], "decryption": "none"}
    assert vless[0]["streamSettings"]["network"] == "raw"
    assert vless[0]["streamSettings"]["security"] == "reality"
    assert vless[0]["streamSettings"]["realitySettings"]["serverNames"] == ["www.example.com"]
    assert config["api"] == {"tag": "api", "services": ["HandlerService"]}
    assert len(api) == 1
    assert api[0]["listen"] == "172.31.255.2"
    assert api[0]["port"] == 10085
    assert config["routing"]["rules"] == [
        {"type": "field", "inboundTag": ["api-in"], "outboundTag": "api"}
    ]
    assert vless[0]["streamSettings"]["realitySettings"]["target"] == "93.184.216.34:443"


def test_handler_api_binds_only_to_internal_management_network(tmp_path: Path) -> None:
    compose = _compose()
    management = compose["networks"]["management"]
    xray_management = compose["services"]["xray"]["networks"]["management"]
    config = _render(tmp_path)
    api = next(item for item in config["inbounds"] if item["tag"] == "api-in")

    network = ipaddress.ip_network(management["ipam"]["config"][0]["subnet"])
    listen = ipaddress.ip_address(api["listen"])
    assert management["internal"] is True
    assert listen == ipaddress.ip_address(xray_management["ipv4_address"])
    assert listen in network
    assert api["listen"] not in {"0.0.0.0", "::"}
    assert compose["services"]["xray"]["healthcheck"]["test"][4] == f"--server={listen}:10085"


def test_renderer_does_not_print_private_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _render(tmp_path)
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""


def test_renderer_prefers_secure_private_key_file(tmp_path: Path) -> None:
    secret = tmp_path / "private-key"
    secret.write_text(PRIVATE_KEY + "\n", encoding="ascii")
    secret.chmod(0o600)

    config = _render(
        tmp_path,
        REALITY_PRIVATE_KEY="invalid-direct-value",
        REALITY_PRIVATE_KEY_FILE=str(secret),
    )

    managed = next(item for item in config["inbounds"] if item["tag"] == "vless-managed")
    assert managed["streamSettings"]["realitySettings"]["privateKey"] == PRIVATE_KEY


@pytest.mark.parametrize("kind", ("symlink", "directory", "unsafe-mode", "oversized"))
def test_renderer_rejects_unsafe_private_key_file(
    tmp_path: Path, kind: str
) -> None:
    secret = tmp_path / "private-key"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text(PRIVATE_KEY, encoding="ascii")
        target.chmod(0o600)
        secret.symlink_to(target)
    elif kind == "directory":
        secret.mkdir()
    elif kind == "unsafe-mode":
        secret.write_text(PRIVATE_KEY, encoding="ascii")
        secret.chmod(0o644)
    else:
        secret.write_text("x" * 129, encoding="ascii")
        secret.chmod(0o600)

    with pytest.raises(RuntimeConfigError, match="private key file") as captured:
        _render(tmp_path, REALITY_PRIVATE_KEY_FILE=str(secret))

    assert PRIVATE_KEY not in str(captured.value)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"REALITY_PRIVATE_KEY": ""}, "private key"),
        ({"REALITY_PRIVATE_KEY": "not-a-key"}, "private key"),
        ({"REALITY_TARGET": "127.0.0.1:443"}, "hostname"),
        ({"REALITY_TARGET": "example.com"}, "target"),
        ({"REALITY_SERVER_NAME": "127.0.0.1"}, "server name"),
        ({"REALITY_SHORT_IDS": "xyz"}, "short ID"),
        ({"REALITY_SHORT_IDS": "abc"}, "short ID"),
        ({"REALITY_SHORT_IDS": ""}, "short ID"),
    ),
)
def test_invalid_reality_input_fails_closed(
    tmp_path: Path, overrides: dict[str, str], message: str
) -> None:
    with pytest.raises(RuntimeConfigError, match=message):
        _render(tmp_path, **overrides)


@pytest.mark.parametrize(
    "address",
    (
        "127.0.0.1",
        "10.0.0.1",
        "169.254.10.1",
        "169.254.169.254",
        "224.0.0.1",
        "0.0.0.0",
        "192.0.2.1",
        "::1",
        "fe80::1",
    ),
)
def test_forbidden_dns_resolution_fails_before_tls(tmp_path: Path, address: str) -> None:
    tls_calls: list[object] = []

    with pytest.raises(RuntimeConfigError, match="public"):
        render_xray_config(
            environment=_environment(),
            template_path=ROOT / "xray/config.template.json",
            output_path=tmp_path / "config.json",
            resolver=lambda *_: [(0, 0, 0, "", (address, 443))],
            tls_connector=lambda *args, **kwargs: tls_calls.append((args, kwargs)) or "TLSv1.3",
        )

    assert tls_calls == []
    assert not (tmp_path / "config.json").exists()
    parsed = ipaddress.ip_address(address)
    assert not parsed.is_global or parsed.is_multicast


def test_dns_failure_and_any_mixed_private_answer_fail_closed(tmp_path: Path) -> None:
    def failed(*_: object) -> list[tuple[object, ...]]:
        raise OSError("resolver detail")

    for resolver in (
        failed,
        lambda *_: [
            (0, 0, 0, "", ("93.184.216.34", 443)),
            (0, 0, 0, "", ("10.0.0.1", 443)),
        ],
    ):
        with pytest.raises(RuntimeConfigError):
            render_xray_config(
                environment=_environment(),
                template_path=ROOT / "xray/config.template.json",
                output_path=tmp_path / "config.json",
                resolver=resolver,
                tls_connector=_tls_13,
            )


def test_blocked_dns_resolver_is_abandoned_at_wall_clock_deadline(tmp_path: Path) -> None:
    release = threading.Event()

    def blocked(*_: object) -> list[tuple[object, ...]]:
        release.wait(10)
        return _public_resolution()

    started = time.monotonic()
    try:
        with pytest.raises(RuntimeConfigError, match="DNS resolution failed"):
            render_xray_config(
                environment=_environment(),
                template_path=ROOT / "xray/config.template.json",
                output_path=tmp_path / "config.json",
                resolver=blocked,
                tls_connector=_tls_13,
                timeout_seconds=0.05,
            )
    finally:
        release.set()

    assert time.monotonic() - started < 0.3
    assert not (tmp_path / "config.json").exists()


def test_tls_preflight_uses_sni_bounded_timeout_and_requires_tls_13(tmp_path: Path) -> None:
    calls: list[tuple[str, int, str, float]] = []

    def connector(host: str, port: int, sni: str, timeout: float) -> str:
        calls.append((host, port, sni, timeout))
        return "TLSv1.3"

    render_xray_config(
        environment=_environment(),
        template_path=ROOT / "xray/config.template.json",
        output_path=tmp_path / "config.json",
        resolver=_public_resolution,
        tls_connector=connector,
    )

    assert calls == [("93.184.216.34", 443, "www.example.com", 5.0)]

    for connector_failure in (
        lambda *_: "TLSv1.2",
        lambda *_: (_ for _ in ()).throw(OSError("certificate failure")),
    ):
        with pytest.raises(RuntimeConfigError, match="TLS"):
            render_xray_config(
                environment=_environment(),
                template_path=ROOT / "xray/config.template.json",
                output_path=tmp_path / "failed.json",
                resolver=_public_resolution,
                tls_connector=connector_failure,
            )


def test_reality_target_uses_first_validated_ip_after_all_preflights(tmp_path: Path) -> None:
    calls: list[str] = []
    resolution = [
        (2, 1, 6, "", ("93.184.216.34", 443)),
        (10, 1, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0)),
    ]

    config = render_xray_config(
        environment=_environment(),
        template_path=ROOT / "xray/config.template.json",
        output_path=tmp_path / "config.json",
        resolver=lambda *_: resolution,
        tls_connector=lambda address, *_: calls.append(address) or "TLSv1.3",
    )

    managed = next(item for item in config["inbounds"] if item["tag"] == "vless-managed")
    reality = managed["streamSettings"]["realitySettings"]
    assert calls == ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]
    assert reality["target"] == "93.184.216.34:443"
    assert reality["serverNames"] == ["www.example.com"]


def test_reality_ipv6_target_is_bracketed(tmp_path: Path) -> None:
    config = render_xray_config(
        environment=_environment(),
        template_path=ROOT / "xray/config.template.json",
        output_path=tmp_path / "config.json",
        resolver=lambda *_: [
            (10, 1, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0))
        ],
        tls_connector=_tls_13,
    )

    managed = next(item for item in config["inbounds"] if item["tag"] == "vless-managed")
    assert managed["streamSettings"]["realitySettings"]["target"] == (
        "[2606:2800:220:1:248:1893:25c8:1946]:443"
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("flow", ""),
        ("security", "none"),
        ("decryption", "auto"),
    ),
)
def test_renderer_rejects_wrong_managed_inbound_semantics(
    tmp_path: Path, field: str, replacement: str
) -> None:
    template = json.loads((ROOT / "xray/config.template.json").read_text(encoding="utf-8"))
    managed = next(item for item in template["inbounds"] if item["tag"] == "vless-managed")
    if field == "flow":
        managed["_managedClientFlow"] = replacement
    elif field == "security":
        managed["streamSettings"][field] = replacement
    else:
        managed["settings"][field] = replacement
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(template), encoding="utf-8")

    with pytest.raises(RuntimeConfigError, match=field):
        render_xray_config(
            environment=_environment(),
            template_path=broken,
            output_path=tmp_path / "config.json",
            resolver=_public_resolution,
            tls_connector=_tls_13,
        )


def test_renderer_rejects_non_object_inbound_without_internal_error(tmp_path: Path) -> None:
    template = json.loads((ROOT / "xray/config.template.json").read_text(encoding="utf-8"))
    template["inbounds"].append(None)
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(template), encoding="utf-8")

    with pytest.raises(RuntimeConfigError, match="inbounds"):
        render_xray_config(
            environment=_environment(),
            template_path=broken,
            output_path=tmp_path / "config.json",
            resolver=_public_resolution,
            tls_connector=_tls_13,
        )


class _FakeXray:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *, accesses: tuple[object, ...]) -> None:
        self.calls.append(accesses)


def _snapshot(revision: int, with_access: bool) -> SnapshotDTO:
    accesses = []
    if with_access:
        accesses.append(
            {
                "access_id": 7,
                "uuid": "01890f47-a2d4-7c11-b3e6-89f40d8639f1",
                "access_revision": revision,
            }
        )
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "snapshot_revision": revision,
        "accesses": accesses,
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload["snapshot_hash"] = hashlib.sha256(canonical).hexdigest()
    return SnapshotDTO.model_validate(payload)


@pytest.mark.parametrize("with_access", (False, True), ids=("empty", "non-empty"))
def test_persisted_snapshot_restores_before_recovery_readiness(
    tmp_path: Path, with_access: bool
) -> None:
    store = SnapshotStore(path=tmp_path / "snapshot.json")
    snapshot = _snapshot(1, with_access)
    store.save(snapshot=snapshot)
    xray = _FakeXray()
    state = AgentRuntimeState(observer=Observability())
    initialize = InitializeRuntimeService(
        state=state,
        restore=StartupRestoreService(apply_accesses=xray, store=store),
    )

    assert state.read().readiness is Readiness.NOT_READY
    initialize()

    assert xray.calls == [snapshot.accesses]
    assert state.read().readiness is Readiness.RECOVERY_READY


def test_health_evidence_is_fixed_and_never_contains_private_key() -> None:
    compose = _compose()
    environment = compose["services"]["agent"]["environment"]

    assert environment["XRAY_VERSION"] == PINNED_XRAY_VERSION
    assert environment["XRAY_IMAGE_DIGEST"] == PINNED_XRAY_DIGEST
    assert PRIVATE_KEY not in json.dumps(environment)
