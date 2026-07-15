from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.release_evidence import EvidenceValidationError, validate_release_evidence


HEAD = "1" * 40
XRAY_DIGEST = "sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f"
PROVENANCE_DIGEST = "ce97974d13f1f7b417feed1549cc08cc8aa0a9c5e8bfc26da03b99c8bd3e4763"
BOOTSTRAP_SHA = "fcc8f8a678638d97247a68cc6b17d3dfe0473ff2"


def valid_evidence() -> dict[str, object]:
    return {
        "candidate_sha": HEAD,
        "ci_sha": HEAD,
        "reviewed_sha": HEAD,
        "test_deployed_sha": HEAD,
        "review_verdict": "approved",
        "contract": {"major": "v1", "snapshot_schema": "1.0"},
        "xray": {"version": "26.7.11", "image_digest": XRAY_DIGEST},
        "backend_fixture": {
            "repository": "my-mtproto-backend",
            "source_commit": "507d152a3f3a404b9348ba3d57906f1dc225558c",
            "provenance_sha256": PROVENANCE_DIGEST,
        },
        "checks": {
            "full_suite": "pass",
            "contract_parity": "pass",
            "compose": "pass",
            "ansible_test_syntax": "pass",
            "ansible_production_syntax": "pass",
            "authenticated_https_health_ready": "pass",
            "empty_apply": "pass",
            "non_empty_apply": "pass",
            "stale_rejection": "pass",
            "conflict_rejection": "pass",
            "overflow_rejection": "pass",
            "restart_restore": "pass",
            "compatible_rollback_rehearsal": "pass",
            "forward_redeploy": "pass",
        },
        "runtime": {
            "deployed_agent_sha": HEAD,
            "health_agent_sha": HEAD,
            "xray_version": "26.7.11",
            "xray_image_digest": XRAY_DIGEST,
            "bootstrap_agent_sha": BOOTSTRAP_SHA,
            "rollback_health_agent_sha": BOOTSTRAP_SHA,
            "forward_deployed_agent_sha": HEAD,
            "forward_health_agent_sha": HEAD,
        },
    }


def test_accepts_complete_evidence_for_exact_expected_head() -> None:
    validate_release_evidence(valid_evidence(), expected_head=HEAD)


def test_rejects_bootstrap_as_forward_candidate() -> None:
    evidence = valid_evidence()
    for field in ("candidate_sha", "ci_sha", "reviewed_sha", "test_deployed_sha"):
        evidence[field] = BOOTSTRAP_SHA
    evidence["runtime"]["deployed_agent_sha"] = BOOTSTRAP_SHA
    evidence["runtime"]["health_agent_sha"] = BOOTSTRAP_SHA
    evidence["runtime"]["forward_deployed_agent_sha"] = BOOTSTRAP_SHA
    evidence["runtime"]["forward_health_agent_sha"] = BOOTSTRAP_SHA

    with pytest.raises(EvidenceValidationError) as caught:
        validate_release_evidence(evidence, expected_head=BOOTSTRAP_SHA)

    assert str(caught.value) == "expected_head:value"


@pytest.mark.parametrize(
    ("mutation", "path", "reason"),
    [
        (lambda value: value.pop("reviewed_sha"), "reviewed_sha", "missing"),
        (lambda value: value.pop("ci_sha"), "ci_sha", "missing"),
        (lambda value: value.__setitem__("ci_sha", "main"), "ci_sha", "format"),
        (lambda value: value.__setitem__("ci_sha", "2" * 40), "ci_sha", "mismatch"),
        (lambda value: value.__setitem__("notes", "safe-looking"), "evidence", "unknown"),
        (lambda value: value.__setitem__("candidate_sha", "main"), "candidate_sha", "format"),
        (lambda value: value.__setitem__("reviewed_sha", "A" * 40), "reviewed_sha", "format"),
        (lambda value: value.__setitem__("test_deployed_sha", "1" * 39), "test_deployed_sha", "format"),
        (lambda value: value.__setitem__("candidate_sha", "2" * 40), "candidate_sha", "mismatch"),
        (lambda value: value.__setitem__("review_verdict", "pending"), "review_verdict", "value"),
        (lambda value: value["contract"].__setitem__("major", "v2"), "contract.major", "value"),
        (lambda value: value["contract"].__setitem__("snapshot_schema", 1.0), "contract.snapshot_schema", "type"),
        (lambda value: value["xray"].__setitem__("version", "latest"), "xray.version", "value"),
        (lambda value: value["xray"].__setitem__("image_digest", "26.7.11"), "xray.image_digest", "value"),
        (lambda value: value["backend_fixture"].__setitem__("repository", "other"), "backend_fixture.repository", "value"),
        (lambda value: value["backend_fixture"].__setitem__("source_commit", "main"), "backend_fixture.source_commit", "value"),
        (lambda value: value["backend_fixture"].__setitem__("provenance_sha256", "0" * 64), "backend_fixture.provenance_sha256", "value"),
        (lambda value: value["checks"].__setitem__("empty_apply", "skipped"), "checks.empty_apply", "value"),
        (lambda value: value["checks"].__setitem__("full_suite", True), "checks.full_suite", "type"),
        (lambda value: value["checks"].pop("restart_restore"), "checks.restart_restore", "missing"),
        (lambda value: value["checks"].__setitem__("smoke_notes", "ok"), "checks", "unknown"),
        (lambda value: value["runtime"].__setitem__("health_agent_sha", "2" * 40), "runtime.health_agent_sha", "mismatch"),
        (lambda value: value["runtime"].__setitem__("xray_version", "26.7.10"), "runtime.xray_version", "value"),
        (lambda value: value["checks"].pop("compatible_rollback_rehearsal"), "checks.compatible_rollback_rehearsal", "missing"),
        (lambda value: value["checks"].__setitem__("forward_redeploy", "skipped"), "checks.forward_redeploy", "value"),
        (lambda value: value["runtime"].pop("bootstrap_agent_sha"), "runtime.bootstrap_agent_sha", "missing"),
        (lambda value: value["runtime"].__setitem__("bootstrap_agent_sha", "main"), "runtime.bootstrap_agent_sha", "format"),
        (lambda value: value["runtime"].__setitem__("bootstrap_agent_sha", "2" * 40), "runtime.bootstrap_agent_sha", "value"),
        (lambda value: value["runtime"].__setitem__("rollback_health_agent_sha", "2" * 40), "runtime.rollback_health_agent_sha", "value"),
        (lambda value: value["runtime"].__setitem__("forward_deployed_agent_sha", "2" * 40), "runtime.forward_deployed_agent_sha", "mismatch"),
        (lambda value: value["runtime"].__setitem__("forward_health_agent_sha", "2" * 40), "runtime.forward_health_agent_sha", "mismatch"),
    ],
)
def test_rejects_incomplete_or_non_exact_evidence(mutation, path: str, reason: str) -> None:
    evidence = valid_evidence()
    mutation(evidence)

    with pytest.raises(EvidenceValidationError) as caught:
        validate_release_evidence(evidence, expected_head=HEAD)

    assert caught.value.path == path
    assert caught.value.reason == reason
    assert str(caught.value) == f"{path}:{reason}"


@pytest.mark.parametrize("expected_head", ["main", "1" * 39, "A" * 40])
def test_rejects_non_exact_expected_head(expected_head: str) -> None:
    with pytest.raises(EvidenceValidationError) as caught:
        validate_release_evidence(valid_evidence(), expected_head=expected_head)

    assert str(caught.value) == "expected_head:format"


def test_error_never_contains_rejected_value() -> None:
    evidence = valid_evidence()
    secret = "sensitive-token-that-must-not-be-logged"
    evidence["review_verdict"] = secret

    with pytest.raises(EvidenceValidationError) as caught:
        validate_release_evidence(evidence, expected_head=HEAD)

    assert secret not in str(caught.value)


def test_error_never_contains_unknown_caller_controlled_field_name() -> None:
    evidence = valid_evidence()
    secret = "sensitive-token-used-as-a-field-name"
    evidence[secret] = "value"

    with pytest.raises(EvidenceValidationError) as caught:
        validate_release_evidence(evidence, expected_head=HEAD)

    assert str(caught.value) == "evidence:unknown"
    assert secret not in str(caught.value)


def test_rejects_if_checked_local_provenance_content_changes(tmp_path: Path) -> None:
    provenance = tmp_path / "PROVENANCE.sha256"
    provenance.write_text(
        "source_repository=my-mtproto-backend\n"
        "source_commit=507d152a3f3a404b9348ba3d57906f1dc225558c\n"
        "changed content\n",
        encoding="utf-8",
    )

    with pytest.raises(EvidenceValidationError) as caught:
        validate_release_evidence(valid_evidence(), expected_head=HEAD, provenance_path=provenance)

    assert str(caught.value) == "backend_fixture.provenance_file:content"


def test_cli_reports_only_safe_path_and_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from src.release_evidence import main

    secret = "sensitive-token-that-must-not-be-logged"
    evidence = valid_evidence()
    evidence["review_verdict"] = secret
    evidence_path = tmp_path / "release-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    exit_code = main(["--expected-head", HEAD, str(evidence_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "release evidence invalid: review_verdict:value\n"
    assert secret not in captured.err


def test_cli_rejects_invalid_json_without_echoing_contents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from src.release_evidence import main

    evidence_path = tmp_path / "release-evidence.json"
    evidence_path.write_text('{"token":"secret",', encoding="utf-8")

    exit_code = main(["--expected-head", HEAD, str(evidence_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == "release evidence invalid: evidence:json\n"


def test_cli_rejects_invalid_utf8_without_echoing_path_or_contents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from src.release_evidence import main

    secret = "sensitive-path-component"
    evidence_path = tmp_path / f"{secret}.json"
    evidence_path.write_bytes(b'\xff{"token":"sensitive-value"}')

    exit_code = main(["--expected-head", HEAD, str(evidence_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == "release evidence invalid: evidence:encoding\n"
    assert secret not in captured.err
    assert "sensitive-value" not in captured.err


@pytest.mark.parametrize(
    ("document", "safe_path", "duplicate_key"),
    [
        ('{"candidate_sha":"first","candidate_sha":"second"}', "evidence", "candidate_sha"),
        ('{"contract":{"major":"v1","major":"v2"}}', "contract", "major"),
        ('{"contract":[{"major":"v1","major":"v2"}]}', "contract", "major"),
        ('{"sensitive-key":1,"sensitive-key":2}', "evidence", "sensitive-key"),
    ],
)
def test_cli_rejects_duplicate_keys_without_reflecting_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    document: str,
    safe_path: str,
    duplicate_key: str,
) -> None:
    from src.release_evidence import main

    evidence_path = tmp_path / "release-evidence.json"
    evidence_path.write_text(document, encoding="utf-8")

    exit_code = main(["--expected-head", HEAD, str(evidence_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == f"release evidence invalid: {safe_path}:duplicate\n"
    assert duplicate_key not in captured.err


def test_cli_rejects_parser_recursion_without_traceback_or_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from src.release_evidence import main

    secret = "sensitive-parser-depth-path"
    evidence_path = tmp_path / f"{secret}.json"
    evidence_path.write_text("[" * 2_000 + "]" * 2_000, encoding="utf-8")

    exit_code = main(["--expected-head", HEAD, str(evidence_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == "release evidence invalid: evidence:json\n"
    assert secret not in captured.err


def test_cli_handles_deep_duplicate_traversal_without_recursion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from src.release_evidence import main

    depth = 700
    document = (
        '{"contract":'
        + "[" * depth
        + '{"major":"v1","major":"v2"}'
        + "]" * depth
        + "}"
    )
    evidence_path = tmp_path / "release-evidence.json"
    evidence_path.write_text(document, encoding="utf-8")

    exit_code = main(["--expected-head", HEAD, str(evidence_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == "release evidence invalid: contract:duplicate\n"
    assert "major" not in captured.err


def test_cli_rejects_huge_integer_without_traceback_or_value_echo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from src.release_evidence import main

    evidence_path = tmp_path / "release-evidence.json"
    evidence_path.write_text("1" * 5_000, encoding="utf-8")

    exit_code = main(["--expected-head", HEAD, str(evidence_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == "release evidence invalid: evidence:json\n"
    assert "111111" not in captured.err


def test_cli_rejects_oversized_input_before_parsing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from src.release_evidence import MAX_EVIDENCE_BYTES, main

    evidence_path = tmp_path / "release-evidence.json"
    evidence_path.write_bytes(b"{" + b"s" * MAX_EVIDENCE_BYTES)

    exit_code = main(["--expected-head", HEAD, str(evidence_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == "release evidence invalid: evidence:size\n"


def test_invalid_utf8_provenance_fails_with_safe_content_reason(tmp_path: Path) -> None:
    provenance = tmp_path / "sensitive-provenance-path"
    provenance.write_bytes(b"\xffsource_repository=my-mtproto-backend")

    with pytest.raises(EvidenceValidationError) as caught:
        validate_release_evidence(valid_evidence(), expected_head=HEAD, provenance_path=provenance)

    assert str(caught.value) == "backend_fixture.provenance_file:content"
    assert provenance.name not in str(caught.value)


def test_rejects_if_local_compatibility_matrix_does_not_contain_reviewed_bootstrap(
    tmp_path: Path,
) -> None:
    compatibility = tmp_path / "COMPATIBILITY.md"
    compatibility.write_text(
        "| contract v1 | snapshot schema 1.0 | 26.7.11 / digest | "
        "`2222222222222222222222222222222222222222` | backend | gates |\n",
        encoding="utf-8",
    )

    with pytest.raises(EvidenceValidationError) as caught:
        validate_release_evidence(
            valid_evidence(),
            expected_head=HEAD,
            compatibility_path=compatibility,
        )

    assert str(caught.value) == "runtime.rollback_compatibility_file:content"


def test_cli_accepts_valid_bounded_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from src.release_evidence import main

    evidence_path = tmp_path / "release-evidence.json"
    evidence_path.write_text(json.dumps(valid_evidence()), encoding="utf-8")

    exit_code = main(["--expected-head", HEAD, str(evidence_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "release evidence valid\n"
    assert captured.err == ""
