# VLESS VDS instance agent

FastAPI agent for a VLESS node managed by the central subscription backend.
This initial scaffold intentionally exposes no HTTP endpoints.

## Requirements

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Docker with Compose (optional)

## Local setup

Install exactly the dependencies recorded in the lockfile:

```bash
uv sync --frozen
```

Set the required public node identity and start the application factory:

```bash
export VLESS_NODE_ID=local-node
export ENVIRONMENT_MODE=local
export AGENT_TOKEN_CURRENT="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
make run
```

`VLESS_NODE_ID` must be a non-empty identifier assigned to this node.
`ENVIRONMENT_MODE` accepts `local`, `test`, or `production` and defaults to
`production`. Startup fails before serving when the node identity is absent.
Production also requires a unique per-node `AGENT_TOKEN_CURRENT` of at least 32
characters. Generate tokens with a cryptographically secure generator, such as
`python -c 'import secrets; print(secrets.token_urlsafe(32))'`; never commit or
log their values. To rotate without downtime, set a separately generated
`AGENT_TOKEN_NEXT`, switch the backend after health/reconcile succeeds, then
promote next to current and remove the overlap value.

Every agent endpoint, including health, must be exposed only through HTTPS with
certificate verification enabled. Plain HTTP is not a supported deployment,
including on private networks.

Run tests and validate production Compose:

```bash
make test
make compose-config
```

## Docker

Start the local container with a source bind mount and reload enabled:

```bash
docker compose -f docker-compose.local.yml up --build
```

Production Compose does not mount source code and requires the node identity
and bearer token:

```bash
VLESS_NODE_ID=example-node AGENT_TOKEN_CURRENT=secure-token-from-secret-store-at-least-32-chars docker compose -f docker-compose.yml up --build -d
```

The image runs the agent as a non-root user. No secrets or environment files
are copied into it. A public health route will be added only with the reviewed
agent API contract.

## Layout

```text
src/app.py          empty FastAPI application factory
src/config.py       typed environment settings
tests/unit/         focused unit tests
Dockerfile          non-root production image
docker-compose.yml  production container definition
```

## Authority boundary

The central backend remains the source of truth for subscriptions and node
access. Xray is a source-derived runtime that will receive reviewed exact-set
snapshots from that backend. 3x-ui is not an authoritative writer and must not
be used to mutate the managed access set.

The agent exclusively owns users on its configured managed inbound. It derives
stable identities as `vless-access-<access_id>@agent.invalid`, reconciles that
inbound to the exact desired UUID set, and never mutates any other inbound tag.
The adapter is tested with Xray 26.7.11 against `HandlerService` operations
`GetInboundUsers` and `AlterInbound` using `AddUserOperation` and
`RemoveUserOperation`. The Xray gRPC management listener must remain private and
must never be exposed to the public network.

## Durable snapshot and startup recovery

The central backend remains authoritative. The agent's local snapshot is only
the last durably accepted exact set and is a startup recovery cache, not proof
that the node matches the backend's current desired revision. An apply succeeds
only after Xray accepts the complete validated access set and the complete
snapshot has been flushed, atomically renamed, and directory-synced.

The snapshot directory is created with mode `0700`; the snapshot and its unique
same-directory temporary file use exact mode `0600`. The JSON contains only the
schema version, revision, hash, and managed accesses (including their inherent
UUIDs). It must be stored on a private persistent volume and must not be logged,
published, or edited by hand. Each newly created cache-directory entry is
synced through its containing directory before snapshot persistence continues.

On startup, a missing snapshot is a clean first boot and the agent stays not
ready. A valid durable snapshot is validated and reapplied to Xray, after which
the agent is only `recovery-ready`; central health and reconcile must still
confirm the current desired snapshot before the node can serve subscriptions.
Torn JSON, an invalid hash/schema/order, a symlink or non-regular file, or any
mode other than `0600` blocks recovery and readiness without changing Xray.

If recovery is blocked, keep the node out of service, preserve the suspect file
for private forensic inspection, and restore or remove it only through the
operator's approved recovery procedure. Removing it deliberately returns the
agent to clean-first-boot/not-ready state; trigger a full backend reconcile
before returning the node to service. Never repair snapshot content or file
permissions merely to bypass validation.
