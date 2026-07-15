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
