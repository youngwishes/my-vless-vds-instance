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
make run
```

`VLESS_NODE_ID` must be a non-empty identifier assigned to this node.
`ENVIRONMENT_MODE` accepts `local`, `test`, or `production` and defaults to
`production`. Startup fails before serving when the node identity is absent.

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

Production Compose does not mount source code and requires the node identity:

```bash
VLESS_NODE_ID=example-node docker compose -f docker-compose.yml up --build -d
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
