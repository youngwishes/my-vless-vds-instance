# Immutable release evidence

A production candidate is one exact lowercase 40-character Git SHA. Review,
CI, test deployment, smoke results, and authenticated runtime health must all
refer to that same SHA. The evidence JSON is an external operator artifact:
`release-evidence*.json` is ignored and a filled report must never be committed.

The direct-bridge bootstrap evidence is also external. The reviewed bootstrap
SHA is `fcc8f8a678638d97247a68cc6b17d3dfe0473ff2`; the compatibility matrix binds
it as the compatible direct-bridge rollback point. Docker 29.6 loopback baseline
`564dc521016cc7463f7e7870ceb159b60883cccb` remains historical A-007 evidence,
not an operational direct-bridge rollback target.

## Closed evidence schema

The validator accepts exactly the fields below. Replace every placeholder with
the result produced for the candidate; every check must be the string `pass`.
No additional fields or duplicate JSON keys are allowed. The UTF-8 encoded file
must not exceed 64 KiB.

```json
{
  "candidate_sha": "<exact-lowercase-40-hex-sha>",
  "ci_sha": "<same-exact-sha>",
  "reviewed_sha": "<same-exact-sha>",
  "test_deployed_sha": "<same-exact-sha>",
  "review_verdict": "approved",
  "contract": {
    "major": "v1",
    "snapshot_schema": "1.0"
  },
  "xray": {
    "version": "26.7.11",
    "image_digest": "sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f"
  },
  "backend_fixture": {
    "repository": "my-mtproto-backend",
    "source_commit": "507d152a3f3a404b9348ba3d57906f1dc225558c",
    "provenance_sha256": "ce97974d13f1f7b417feed1549cc08cc8aa0a9c5e8bfc26da03b99c8bd3e4763"
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
    "forward_redeploy": "pass"
  },
  "runtime": {
    "deployed_agent_sha": "<same-exact-sha>",
    "health_agent_sha": "<same-exact-sha>",
    "xray_version": "26.7.11",
    "xray_image_digest": "sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f",
    "bootstrap_agent_sha": "fcc8f8a678638d97247a68cc6b17d3dfe0473ff2",
    "rollback_health_agent_sha": "fcc8f8a678638d97247a68cc6b17d3dfe0473ff2",
    "forward_deployed_agent_sha": "<same-exact-sha>",
    "forward_health_agent_sha": "<same-exact-sha>"
  }
}
```

The validator also hashes the repository's fixed
`docs/contracts/v1/PROVENANCE.sha256` and verifies its reviewed backend
repository and source commit. A report cannot redirect this check to a caller-
selected file or fixture.

`authenticated_https_health_ready: pass` means the authenticated request used
verified HTTPS, health reported `READY`, and the response carried the exact
candidate agent SHA plus pinned Xray version and image digest. HTTP 200 alone is
not a pass. `bootstrap_agent_sha` binds the compatible rollback rehearsal to the
reviewed bootstrap in `COMPATIBILITY.md`, and `rollback_health_agent_sha` proves
health after returning to that exact bootstrap. `forward_deployed_agent_sha` and
`forward_health_agent_sha` must both match the candidate, while
`forward_redeploy: pass` records the separate successful forward operation. The
validator also verifies the exact contract v1, snapshot schema 1.0, Xray
version/digest and bootstrap SHA matrix row locally.

Do not add IP addresses, hostnames, bearer tokens, certificate content, UUIDs,
snapshot bodies or hashes, REALITY keys, subscription URLs, free-form notes, or
other fields. Keep operational secrets in the approved secret mechanism and
record only the closed safe status vocabulary above. Validator failures contain
only a field path and reason code; they never echo rejected values.

## Operator gate

Run from a clean candidate checkout and pass HEAD explicitly:

```bash
uv run python -m src.release_evidence \
  --expected-head "$(git rev-parse HEAD)" \
  /external/protected/release-evidence.json
```

Exit zero means only that this report is structurally complete and bound to the
current exact HEAD. `ci_sha` is the exact checked-out revision derived by CI
with `git rev-parse HEAD`, not a floating event or merge ref. Any tracked change
after CI, review, or test deployment creates a new candidate SHA and invalidates
that evidence; repeat CI, review, test deployment, smoke, and validation for the
new SHA. Never edit a filled report to carry results forward.

Validator success does **not** authorize production deployment. Immediately
before production, follow `DEPLOY.md` and obtain fresh explicit user approval
for the exact validated SHA and named production inventory.
