from __future__ import annotations

import base64
import ipaddress
import json
import os
import queue
import socket
import ssl
import stat
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, final

PINNED_XRAY_VERSION = "26.7.11"
PINNED_XRAY_DIGEST = "sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f"
PINNED_XRAY_IMAGE = f"ghcr.io/xtls/xray-core@{PINNED_XRAY_DIGEST}"
_METADATA_ADDRESSES = frozenset(
    map(ipaddress.ip_address, ("169.254.169.254", "100.100.100.200", "fd00:ec2::254"))
)
Resolver = Callable[..., Sequence[tuple[object, ...]]]
TLSConnector = Callable[[str, int, str, float], str]
_MAX_PRIVATE_KEY_FILE_BYTES = 128


@final
class RuntimeConfigError(ValueError):
    """The Xray runtime configuration failed closed."""


def _required(environment: Mapping[str, str], name: str, label: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise RuntimeConfigError(f"{label} is required")
    return value


def _private_key_from_file(path: str) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_PRIVATE_KEY_FILE_BYTES
            or metadata.st_mode & 0o077
            or metadata.st_uid not in {0, os.geteuid()}
        ):
            raise RuntimeConfigError("REALITY private key file is unsafe")
        payload = os.read(descriptor, _MAX_PRIVATE_KEY_FILE_BYTES + 1)
        if len(payload) > _MAX_PRIVATE_KEY_FILE_BYTES:
            raise RuntimeConfigError("REALITY private key file is unsafe")
        try:
            return payload.decode("ascii").strip()
        except UnicodeDecodeError:
            raise RuntimeConfigError("REALITY private key file is unsafe") from None
    except RuntimeConfigError:
        raise
    except OSError:
        raise RuntimeConfigError("REALITY private key file is unavailable or unsafe") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _private_key(environment: Mapping[str, str]) -> str:
    file_path = environment.get("REALITY_PRIVATE_KEY_FILE")
    if file_path is not None:
        stripped_path = file_path.strip()
        if not stripped_path:
            raise RuntimeConfigError("REALITY private key file is required")
        value = _private_key_from_file(stripped_path)
    else:
        value = _required(environment, "REALITY_PRIVATE_KEY", "REALITY private key")
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error):
        raise RuntimeConfigError("REALITY private key is malformed") from None
    if len(value) != 43 or len(decoded) != 32 or not any(decoded):
        raise RuntimeConfigError("REALITY private key is malformed")
    return value


def _hostname(value: str, *, label: str) -> str:
    candidate = value.rstrip(".")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise RuntimeConfigError(f"{label} must be a hostname, not an IP literal")
    try:
        ascii_value = candidate.encode("idna").decode("ascii")
    except UnicodeError:
        raise RuntimeConfigError(f"{label} is malformed") from None
    labels = ascii_value.split(".")
    if (
        len(ascii_value) > 253
        or len(labels) < 2
        or any(not part or len(part) > 63 for part in labels)
        or any(part.startswith("-") or part.endswith("-") for part in labels)
        or any(not part.replace("-", "").isalnum() for part in labels)
    ):
        raise RuntimeConfigError(f"{label} is malformed")
    return ascii_value.lower()


def _target(environment: Mapping[str, str]) -> tuple[str, int]:
    value = _required(environment, "REALITY_TARGET", "REALITY target")
    host, separator, raw_port = value.rpartition(":")
    if not separator or not host or not raw_port.isascii() or not raw_port.isdecimal():
        raise RuntimeConfigError("REALITY target must use hostname:port")
    hostname = _hostname(host, label="REALITY target hostname")
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise RuntimeConfigError("REALITY target port is invalid")
    return hostname, port


def _management_ip(environment: Mapping[str, str]) -> str:
    raw = _required(environment, "XRAY_MANAGEMENT_IP", "Xray management IP")
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        raise RuntimeConfigError("Xray management IP is malformed") from None
    if address.version != 4 or not address.is_private or address.is_unspecified:
        raise RuntimeConfigError("Xray management IP must be a private IPv4 address")
    return address.compressed


def _short_ids(environment: Mapping[str, str]) -> list[str]:
    raw = _required(environment, "REALITY_SHORT_IDS", "REALITY short ID")
    values = [value.strip().lower() for value in raw.split(",")]
    if (
        any(
            not value
            or len(value) > 16
            or len(value) % 2
            or any(character not in "0123456789abcdef" for character in value)
            for value in values
        )
        or len(set(values)) != len(values)
    ):
        raise RuntimeConfigError("REALITY short ID is malformed")
    return values


def _resolve_with_deadline(
    *, host: str, port: int, resolver: Resolver, timeout: float
) -> Sequence[tuple[object, ...]]:
    outcome: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            outcome.put((True, resolver(host, port, 0, socket.SOCK_STREAM)))
        except BaseException as error:
            outcome.put((False, error))

    threading.Thread(target=resolve, name="reality-dns-preflight", daemon=True).start()
    try:
        succeeded, value = outcome.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeConfigError("REALITY target DNS resolution failed") from None
    if not succeeded:
        raise RuntimeConfigError("REALITY target DNS resolution failed") from None
    if not isinstance(value, Sequence):
        raise RuntimeConfigError("REALITY target DNS response is malformed")
    return value


def _public_addresses(
    *, host: str, port: int, resolver: Resolver, timeout: float
) -> tuple[str, ...]:
    try:
        results = _resolve_with_deadline(
            host=host, port=port, resolver=resolver, timeout=timeout
        )
    except RuntimeConfigError:
        raise
    except BaseException:
        raise RuntimeConfigError("REALITY target DNS resolution failed") from None
    addresses: list[str] = []
    for result in results:
        try:
            address = ipaddress.ip_address(str(result[4][0]))  # type: ignore[index]
        except (IndexError, TypeError, ValueError):
            raise RuntimeConfigError("REALITY target DNS response is malformed") from None
        if (
            not address.is_global
            or address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
            or address in _METADATA_ADDRESSES
        ):
            raise RuntimeConfigError("REALITY target must resolve only to public addresses")
        addresses.append(address.compressed)
    if not addresses:
        raise RuntimeConfigError("REALITY target DNS resolution returned no addresses")
    return tuple(dict.fromkeys(addresses))


def _verified_tls_connection(address: str, port: int, sni: str, timeout: float) -> str:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    with socket.create_connection((address, port), timeout=timeout) as connection:
        with context.wrap_socket(connection, server_hostname=sni) as tls:
            return tls.version() or ""


def _preflight(
    *, host: str, port: int, sni: str, resolver: Resolver,
    tls_connector: TLSConnector, timeout: float,
) -> tuple[str, ...]:
    addresses = _public_addresses(
        host=host, port=port, resolver=resolver, timeout=timeout
    )
    for address in addresses:
        try:
            negotiated = tls_connector(address, port, sni, timeout)
        except (OSError, ssl.SSLError, TimeoutError):
            raise RuntimeConfigError("REALITY target TLS verification failed") from None
        if negotiated != "TLSv1.3":
            raise RuntimeConfigError("REALITY target must accept verified TLS 1.3")
    return addresses


def _concrete_target(*, address: str, port: int) -> str:
    parsed = ipaddress.ip_address(address)
    return f"[{parsed.compressed}]:{port}" if parsed.version == 6 else f"{parsed.compressed}:{port}"


def _render(template: dict[str, Any], replacements: Mapping[str, object]) -> dict[str, Any]:
    def replace(value: object) -> object:
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    result = replace(template)
    if not isinstance(result, dict):
        raise RuntimeConfigError("Xray template root must be an object")
    return result


def _validate_semantics(config: dict[str, Any]) -> None:
    inbounds = config.get("inbounds")
    if not isinstance(inbounds, list) or any(
        not isinstance(item, dict) for item in inbounds
    ):
        raise RuntimeConfigError("Xray template inbounds are malformed")
    vless = [item for item in inbounds if item.get("protocol") == "vless"]
    if len(vless) != 1 or vless[0].get("tag") != "vless-managed":
        raise RuntimeConfigError("exactly one managed VLESS inbound is required")
    managed = vless[0]
    settings = managed.get("settings", {})
    stream = managed.get("streamSettings", {})
    if settings.get("clients") != []:
        raise RuntimeConfigError("managed clients must initially be empty")
    if settings.get("decryption") != "none":
        raise RuntimeConfigError("managed decryption must be none")
    if managed.get("_managedClientFlow") != "xtls-rprx-vision":
        raise RuntimeConfigError("managed flow must be xtls-rprx-vision")
    if stream.get("network") != "raw":
        raise RuntimeConfigError("managed network must be raw TCP")
    if stream.get("security") != "reality":
        raise RuntimeConfigError("managed security must be reality")
    if config.get("api") != {"tag": "api", "services": ["HandlerService"]}:
        raise RuntimeConfigError("private HandlerService API wiring is required")
    api_inbounds = [item for item in inbounds if item.get("tag") == "api-in"]
    if len(api_inbounds) != 1 or api_inbounds[0].get("port") != 10085:
        raise RuntimeConfigError("private HandlerService inbound is required")
    try:
        api_listen = ipaddress.ip_address(api_inbounds[0].get("listen", ""))
    except ValueError:
        raise RuntimeConfigError("private HandlerService listen address is malformed") from None
    if api_listen.is_unspecified or not api_listen.is_private:
        raise RuntimeConfigError("private HandlerService must bind a private address")


def _atomic_write(*, path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".config-", delete=False) as temporary:
            temporary_name = temporary.name
            os.fchmod(temporary.fileno(), 0o400)
            json.dump(config, temporary, ensure_ascii=False, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def render_xray_config(
    *, environment: Mapping[str, str], template_path: Path, output_path: Path,
    resolver: Resolver = socket.getaddrinfo,
    tls_connector: TLSConnector = _verified_tls_connection,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    private_key = _private_key(environment)
    host, port = _target(environment)
    management_ip = _management_ip(environment)
    sni = _hostname(_required(environment, "REALITY_SERVER_NAME", "REALITY server name"), label="REALITY server name")
    short_ids = _short_ids(environment)
    raw_public_port = environment.get("VLESS_PUBLIC_PORT", "443").strip()
    if not raw_public_port.isascii() or not raw_public_port.isdecimal() or not 1 <= int(raw_public_port) <= 65535:
        raise RuntimeConfigError("VLESS public port is invalid")
    addresses = _preflight(
        host=host,
        port=port,
        sni=sni,
        resolver=resolver,
        tls_connector=tls_connector,
        timeout=timeout_seconds,
    )
    target = _concrete_target(address=addresses[0], port=port)
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeConfigError("Xray config template is unavailable or malformed") from None
    config = _render(template, {
        "${VLESS_PUBLIC_PORT}": int(raw_public_port),
        "${REALITY_TARGET}": target,
        "${REALITY_SERVER_NAME}": sni,
        "${REALITY_PRIVATE_KEY}": private_key,
        "${REALITY_SHORT_IDS}": short_ids,
        "${XRAY_MANAGEMENT_IP}": management_ip,
    })
    _validate_semantics(config)
    for inbound in config["inbounds"]:
        inbound.pop("_managedClientFlow", None)
    _atomic_write(path=output_path, config=config)
    return config


def main() -> int:
    try:
        render_xray_config(
            environment=os.environ,
            template_path=Path(os.environ.get("XRAY_CONFIG_TEMPLATE", "/app/xray/config.template.json")),
            output_path=Path(os.environ.get("XRAY_CONFIG_OUTPUT", "/generated/config.json")),
        )
    except RuntimeConfigError as error:
        print(str(error), file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("PINNED_XRAY_DIGEST", "PINNED_XRAY_IMAGE", "PINNED_XRAY_VERSION", "RuntimeConfigError", "render_xray_config")
