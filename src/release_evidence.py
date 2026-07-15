from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn


MAX_EVIDENCE_BYTES = 64 * 1024
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_XRAY_VERSION = "26.7.11"
_XRAY_IMAGE_DIGEST = (
    "sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f"
)
_BACKEND_REPOSITORY = "my-mtproto-backend"
_BACKEND_SOURCE_COMMIT = "507d152a3f3a404b9348ba3d57906f1dc225558c"
_PROVENANCE_SHA256 = "ce97974d13f1f7b417feed1549cc08cc8aa0a9c5e8bfc26da03b99c8bd3e4763"
_ROLLBACK_AGENT_SHA = "564dc521016cc7463f7e7870ceb159b60883cccb"
_DEFAULT_PROVENANCE_PATH = (
    Path(__file__).resolve().parents[1] / "docs/contracts/v1/PROVENANCE.sha256"
)
_DEFAULT_COMPATIBILITY_PATH = Path(__file__).resolve().parents[1] / "docs/COMPATIBILITY.md"

_TOP_LEVEL_KEYS = {
    "candidate_sha",
    "ci_sha",
    "reviewed_sha",
    "test_deployed_sha",
    "review_verdict",
    "contract",
    "xray",
    "backend_fixture",
    "checks",
    "runtime",
}
_CONTRACT_KEYS = {"major", "snapshot_schema"}
_XRAY_KEYS = {"version", "image_digest"}
_BACKEND_FIXTURE_KEYS = {"repository", "source_commit", "provenance_sha256"}
_CHECK_KEYS = {
    "full_suite",
    "contract_parity",
    "compose",
    "ansible_test_syntax",
    "ansible_production_syntax",
    "authenticated_https_health_ready",
    "empty_apply",
    "non_empty_apply",
    "stale_rejection",
    "conflict_rejection",
    "overflow_rejection",
    "restart_restore",
    "compatible_rollback_rehearsal",
}
_RUNTIME_KEYS = {
    "deployed_agent_sha",
    "health_agent_sha",
    "xray_version",
    "xray_image_digest",
    "rollback_agent_sha",
}


class EvidenceValidationError(ValueError):
    def __init__(self, *, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}:{reason}")


class _JSONObject(dict[str, object]):
    def __init__(self, pairs: list[tuple[str, object]]) -> None:
        super().__init__(pairs)
        self.has_duplicate = len(self) != len(pairs)


def _fail(path: str, reason: str) -> NoReturn:
    raise EvidenceValidationError(path=path, reason=reason)


def _object(value: object, *, path: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        _fail(path, "type")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], *, path: str = "") -> None:
    if set(value) - expected:
        _fail(path or "evidence", "unknown")
    missing = sorted(expected - set(value))
    if missing:
        _fail(f"{path}.{missing[0]}" if path else missing[0], "missing")


def _string(value: object, *, path: str) -> str:
    if not isinstance(value, str):
        _fail(path, "type")
    if not value:
        _fail(path, "blank")
    return value


def _exact(value: object, expected: str, *, path: str) -> None:
    if _string(value, path=path) != expected:
        _fail(path, "value")


def _sha(value: object, *, path: str, expected_head: str) -> None:
    candidate = _string(value, path=path)
    if _SHA_PATTERN.fullmatch(candidate) is None:
        _fail(path, "format")
    if candidate != expected_head:
        _fail(path, "mismatch")


def _fixed_sha(value: object, *, path: str, expected: str) -> None:
    candidate = _string(value, path=path)
    if _SHA_PATTERN.fullmatch(candidate) is None:
        _fail(path, "format")
    if candidate != expected:
        _fail(path, "value")


def _validate_provenance(path: Path) -> None:
    try:
        content = path.read_bytes()
    except OSError:
        _fail("backend_fixture.provenance_file", "read")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeError:
        _fail("backend_fixture.provenance_file", "content")
    if hashlib.sha256(content).hexdigest() != _PROVENANCE_SHA256:
        _fail("backend_fixture.provenance_file", "content")
    required_lines = {
        f"source_repository={_BACKEND_REPOSITORY}",
        f"source_commit={_BACKEND_SOURCE_COMMIT}",
    }
    if not required_lines.issubset(text.splitlines()):
        _fail("backend_fixture.provenance_file", "content")


def _validate_compatibility(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except OSError:
        _fail("runtime.rollback_compatibility_file", "read")
    except UnicodeError:
        _fail("runtime.rollback_compatibility_file", "content")
    reviewed_row = (
        "| contract v1 | snapshot schema 1.0 | "
        f"{_XRAY_VERSION} / `{_XRAY_IMAGE_DIGEST}` | `{_ROLLBACK_AGENT_SHA}` |"
    )
    if text.count(reviewed_row) != 1:
        _fail("runtime.rollback_compatibility_file", "content")


def _reject_duplicate_keys(value: object, *, path: str = "evidence") -> None:
    if isinstance(value, list):
        for child in value:
            _reject_duplicate_keys(child, path=path)
        return
    if not isinstance(value, _JSONObject):
        return
    if value.has_duplicate:
        _fail(path, "duplicate")
    safe_children = _TOP_LEVEL_KEYS if path == "evidence" else set()
    for key, child in value.items():
        child_path = key if key in safe_children else path
        _reject_duplicate_keys(child, path=child_path)


def validate_release_evidence(
    evidence: object,
    *,
    expected_head: str,
    provenance_path: Path = _DEFAULT_PROVENANCE_PATH,
    compatibility_path: Path = _DEFAULT_COMPATIBILITY_PATH,
) -> None:
    if _SHA_PATTERN.fullmatch(expected_head) is None:
        _fail("expected_head", "format")

    document = _object(evidence, path="evidence")
    _exact_keys(document, _TOP_LEVEL_KEYS)

    for field in ("candidate_sha", "ci_sha", "reviewed_sha", "test_deployed_sha"):
        _sha(document[field], path=field, expected_head=expected_head)
    _exact(document["review_verdict"], "approved", path="review_verdict")

    contract = _object(document["contract"], path="contract")
    _exact_keys(contract, _CONTRACT_KEYS, path="contract")
    _exact(contract["major"], "v1", path="contract.major")
    _exact(contract["snapshot_schema"], "1.0", path="contract.snapshot_schema")

    xray = _object(document["xray"], path="xray")
    _exact_keys(xray, _XRAY_KEYS, path="xray")
    _exact(xray["version"], _XRAY_VERSION, path="xray.version")
    _exact(xray["image_digest"], _XRAY_IMAGE_DIGEST, path="xray.image_digest")

    fixture = _object(document["backend_fixture"], path="backend_fixture")
    _exact_keys(fixture, _BACKEND_FIXTURE_KEYS, path="backend_fixture")
    _exact(fixture["repository"], _BACKEND_REPOSITORY, path="backend_fixture.repository")
    _exact(
        fixture["source_commit"],
        _BACKEND_SOURCE_COMMIT,
        path="backend_fixture.source_commit",
    )
    _exact(
        fixture["provenance_sha256"],
        _PROVENANCE_SHA256,
        path="backend_fixture.provenance_sha256",
    )
    _validate_provenance(provenance_path)

    checks = _object(document["checks"], path="checks")
    _exact_keys(checks, _CHECK_KEYS, path="checks")
    for field in sorted(_CHECK_KEYS):
        _exact(checks[field], "pass", path=f"checks.{field}")

    runtime = _object(document["runtime"], path="runtime")
    _exact_keys(runtime, _RUNTIME_KEYS, path="runtime")
    for field in ("deployed_agent_sha", "health_agent_sha"):
        _sha(runtime[field], path=f"runtime.{field}", expected_head=expected_head)
    _exact(runtime["xray_version"], _XRAY_VERSION, path="runtime.xray_version")
    _exact(
        runtime["xray_image_digest"],
        _XRAY_IMAGE_DIGEST,
        path="runtime.xray_image_digest",
    )
    _fixed_sha(
        runtime["rollback_agent_sha"],
        path="runtime.rollback_agent_sha",
        expected=_ROLLBACK_AGENT_SHA,
    )
    _validate_compatibility(compatibility_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate external immutable release evidence")
    parser.add_argument("evidence_file", type=Path)
    parser.add_argument("--expected-head", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        with arguments.evidence_file.open("rb") as evidence_stream:
            encoded = evidence_stream.read(MAX_EVIDENCE_BYTES + 1)
    except OSError:
        print("release evidence invalid: evidence:read", file=sys.stderr)
        return 1
    if len(encoded) > MAX_EVIDENCE_BYTES:
        print("release evidence invalid: evidence:size", file=sys.stderr)
        return 1
    try:
        raw = encoded.decode("utf-8", errors="strict")
    except UnicodeError:
        print("release evidence invalid: evidence:encoding", file=sys.stderr)
        return 1
    try:
        evidence = json.loads(raw, object_pairs_hook=_JSONObject)
    except (json.JSONDecodeError, UnicodeError):
        print("release evidence invalid: evidence:json", file=sys.stderr)
        return 1
    try:
        _reject_duplicate_keys(evidence)
        validate_release_evidence(evidence, expected_head=arguments.expected_head)
    except EvidenceValidationError as error:
        print(f"release evidence invalid: {error}", file=sys.stderr)
        return 1
    print("release evidence valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
