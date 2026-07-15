# VLESS VDS instance agent

FastAPI agent for a VLESS node managed by the central subscription backend.
Its management API is authenticated and exposes only the reviewed contract-v1
health and exact-snapshot operations.

## Requirements

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Docker with Compose (optional)

## Local setup

Install exactly the dependencies recorded in the lockfile:

```bash
uv sync --frozen
```

Set the required node identity and start the application factory:

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

Production Compose starts a one-shot validated config renderer, the official
distroless Xray image, and then the agent. It requires the node identity,
bearer token, exact agent commit SHA, and runtime-only REALITY inputs:

```bash
VLESS_NODE_ID=example-node \
AGENT_TOKEN_CURRENT=secure-token-from-secret-store-at-least-32-chars \
AGENT_SHA=0123456789abcdef0123456789abcdef01234567 \
REALITY_PRIVATE_KEY_FILE=/secure/runtime/secrets/reality_private_key \
REALITY_TARGET=origin.example:443 \
REALITY_SERVER_NAME=www.example.com \
REALITY_SHORT_IDS=0123456789abcdef \
docker compose -f docker-compose.yml up --build -d
```

Xray is fixed directly in production Compose to official version `26.7.11` at
digest `sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f`;
there is no environment override or floating tag. To upgrade, change the exact
version/digest pair, validate the rendered config against that digest, run the
full tests and a bounded container smoke test, then review the resulting diff.

The REALITY private key is supplied as a Compose secret file, never as an
interpolated service environment value. Create it outside the checkout owned
by runtime uid/gid `65532:65532` with mode `0400` or `0600`; file-backed
Compose secrets do not portably apply requested ownership. The renderer
rejects symlinks, non-regular files,
unexpected ownership, group/other access, and oversized content. The key is
written only to the private generated-config volume. It is never copied into
an image, logged, returned by health, or exposed by `docker compose config`.
The renderer rejects IP-literal targets and any
DNS answer that is loopback, private, link-local, multicast, unspecified,
reserved, or a known metadata address. Before writing config it connects to
each resolved public address with a five-second bound, verified certificates,
configured SNI, and TLS 1.3 minimum; DNS, certificate, SNI, or protocol failure
stops startup. DNS resolution itself runs behind the same wall-clock deadline;
a blocked system resolver is abandoned without delaying startup failure.

Only the VLESS TCP port is public. Xray's HandlerService network is internal,
the agent API is bound to host loopback, roots are read-only, and runtime
containers drop all capabilities and run non-root. All management routes
require bearer authentication and the contract-version header; none is a
public unauthenticated health route.

The internal management network deliberately reserves `172.31.255.0/28` and
assigns Xray `172.31.255.2`. The HandlerService listener binds only that address,
so attaching Xray to the public bridge does not expose port 10085. Verify this
subnet does not overlap host, VPC, or other Docker networks before deployment;
if it does, change the Compose subnet, Xray static address, renderer management
address, healthcheck target, and agent target together and rerun runtime tests.
The one-shot volume initializer also assigns the generated-config volume to uid
65532 and the durable snapshot volume to the pinned agent uid 999 before either
runtime starts.

## Layout

```text
src/app.py          authenticated contract-v1 FastAPI application factory
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

## Contract v1 management API

The authenticated management surface contains only `GET /api/v1/health`,
`GET /api/v1/snapshot`, and `PUT /api/v1/snapshot`. Every request requires both
the node bearer token and `X-Agent-Contract-Version: v1`. Snapshot reads return
revision/hash metadata only; UUIDs are never returned. Exact snapshot PUTs are
serialized, enforce canonical v1 limits and monotonic revisions, and persist
through the durable snapshot service before reporting success.

Startup restore yields `RECOVERY_READY`, never `READY`. Only backend
confirmation of the same revision/hash can promote the node, and only when a
read-only exact-set Xray probe finds no drift. Health may demote readiness but
does not repair Xray. Operational Xray/storage failures use the framework's
generic unadvertised HTTP 500 fallback because contract v1 defines no stable
operational error response; exception details are not returned.
Torn JSON, an invalid hash/schema/order, a symlink or non-regular file, or any
mode other than `0600` blocks recovery and readiness without changing Xray.

If recovery is blocked, keep the node out of service, preserve the suspect file
for private forensic inspection, and restore or remove it only through the
operator's approved recovery procedure. Removing it deliberately returns the
agent to clean-first-boot/not-ready state; trigger a full backend reconcile
before returning the node to service. Never repair snapshot content or file
permissions merely to bypass validation.
