from __future__ import annotations

import re
from pathlib import Path

import yaml


WORKFLOW_PATH = Path(__file__).parents[2] / ".github/workflows/ci.yml"
EXPECTED_ACTIONS = [
    "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
    "astral-sh/setup-uv@b75a909f75acd358c2196fb9a5f1299a9a8868a4",
]


def _workflow() -> tuple[str, dict[str, object]]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_ci_uses_only_exact_official_action_pins_and_read_permission() -> None:
    text, workflow = _workflow()
    uses = re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE)

    assert uses == EXPECTED_ACTIONS
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in workflow["jobs"]["verify"]
    assert "${{ secrets." not in text
    assert "permissions: write" not in text


def test_ci_runs_complete_non_deploy_verification() -> None:
    text, workflow = _workflow()
    steps = workflow["jobs"]["verify"]["steps"]
    by_name = {step["name"]: step for step in steps}
    commands = [step["run"] for step in steps if "run" in step]

    assert "uv sync --locked --all-groups" in commands
    assert "uv run pytest -q" in commands
    assert by_name["Run deployment safety tests"] == {
        "name": "Run deployment safety tests",
        "run": "uv run pytest deploy/tests -q",
    }
    assert "docker compose -f docker-compose.yml config --quiet" in commands
    assert "docker compose -f docker-compose.local.yml config --quiet" in commands
    assert any("deploy/playbook-test.yml --syntax-check" in command for command in commands)
    assert any("deploy/playbook-prod.yml --syntax-check" in command for command in commands)
    assert all("--syntax-check" in command for command in commands if "ansible-playbook" in command)
    assert "docker compose up" not in text


def test_ci_checks_whitespace_across_complete_head_tree() -> None:
    _, workflow = _workflow()
    commands = [
        step["run"]
        for step in workflow["jobs"]["verify"]["steps"]
        if "run" in step
    ]

    assert 'git diff --check "$(git hash-object -t tree /dev/null)" HEAD' in commands


def test_ci_scopes_dummy_interpolation_environment_only_to_compose_steps() -> None:
    _, workflow = _workflow()
    job = workflow["jobs"]["verify"]
    steps = {step["name"]: step for step in job["steps"]}

    assert "env" not in job
    assert "env" not in steps["Run complete test suite"]
    assert "env" not in steps["Syntax-check test deployment"]
    assert "env" not in steps["Syntax-check production deployment"]
    assert steps["Validate production Compose"]["env"] == {
        "AGENT_SHA": "${{ steps.candidate.outputs.sha }}",
        "AGENT_TOKEN_CURRENT": "ci-dummy-token-not-a-credential-0001",
        "REALITY_PRIVATE_KEY_FILE": "/tmp/ci-dummy-reality-private-key",
    }
    assert steps["Validate local Compose"]["env"] == {
        "AGENT_TOKEN_CURRENT": "ci-dummy-token-not-a-credential-0001",
        "REALITY_PRIVATE_KEY_FILE": "/tmp/ci-dummy-reality-private-key",
    }


def test_ci_checks_out_and_consumes_one_exact_candidate_revision() -> None:
    text, workflow = _workflow()
    steps = workflow["jobs"]["verify"]["steps"]
    by_name = {step["name"]: step for step in steps}

    assert by_name["Check out the exact revision"]["with"] == {
        "persist-credentials": False,
        "ref": "${{ github.event.pull_request.head.sha || github.sha }}",
    }
    assert by_name["Derive checked-out candidate SHA"] == {
        "name": "Derive checked-out candidate SHA",
        "id": "candidate",
        "run": "printf 'sha=%s\\n' \"$(git rev-parse HEAD)\" >> \"$GITHUB_OUTPUT\"",
    }
    assert by_name["Validate production Compose"]["env"]["AGENT_SHA"] == (
        "${{ steps.candidate.outputs.sha }}"
    )
    assert 'deploy_revision="${{ steps.candidate.outputs.sha }}"' in by_name[
        "Syntax-check test deployment"
    ]["run"]
    assert 'deploy_revision="${{ steps.candidate.outputs.sha }}"' in by_name[
        "Syntax-check production deployment"
    ]["run"]
    assert "$GITHUB_SHA" not in text
    assert text.count("github.sha") == 1
