# Observability security boundary

The agent exposes no metrics route. One shared, in-process observer is available
as internal application state for tests and local operator integrations. Its
counter names and apply-latency buckets are a closed finite set. Restarting the
process resets them; an external operator-owned collector may retain snapshots.

Structured events have only `event`, fixed enum `code`, and, for apply events,
a bounded numeric `latency_seconds`. The observer cannot accept arbitrary
context or exception objects. Request paths, exception messages, tokens,
headers, node IDs, revisions, hashes, snapshot bodies, UUIDs and keys are never
metric labels or structured fields. Bearer redaction remains defense in depth,
not permission to log sensitive data. Compose bounds container JSON logs to
three 10 MiB files per long-running service; centralized retention and access
control remain operator-owned.

## Safe event catalogue

- `readiness_ready`, `readiness_not_ready`, `readiness_recovery_ready`
- `revision_drift`
- `apply_success`, `apply_failure` (with apply latency)
- `revision_conflict`, `snapshot_overflow`
- `auth_failure`, `incompatible_contract`
- `startup_restore_failure`
- `xray_timeout`, `xray_unavailable`, `xray_protocol_failure`

Suggested alerts are sustained `readiness_not_ready`, a node remaining at
`readiness_recovery_ready` without a later `readiness_ready`, drift, any startup restore failure,
revision conflict or snapshot overflow, repeated apply failure or high latency,
and bursts of auth or Xray failures. Tune thresholds against rollout traffic so
single expected retries do not page. Alert transport, destinations, escalation,
retention and acknowledgement are entirely operator-owned; the agent sends no
alerts and contains no notification credentials.

TLS termination, certificate, nginx, allowlist and pre-agent connection
failures belong to the nginx/deployment plane and must be monitored there. They
do not add an agent route, response field, header, or OpenAPI operation. The v1
surface remains exactly health GET plus snapshot GET/PUT.

TLS is mandatory at the external host nginx listener. The sole plaintext
exception is host nginx and the no-proxy deployment health proof talking to
`172.31.255.3:8000` on the Compose-owned internal `172.31.255.0/28` bridge.
That bridge has gateway `172.31.255.1`, Xray `172.31.255.2`, and agent
`172.31.255.3`; the agent has no host binding. Deployment fails closed on CIDR
overlap, malformed inspection data, ownership drift, endpoint drift, or runtime
topology drift and never repairs a suspect network. Bootstrap evidence stays
external. The compatibility matrix binds the reviewed direct-bridge bootstrap
`fcc8f8a678638d97247a68cc6b17d3dfe0473ff2`; the closed evidence separately
records bootstrap identity, rollback health, and forward deployment and health
without secrets, hosts, or IP addresses.
