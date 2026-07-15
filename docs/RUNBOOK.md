# Agent recovery runbook

The central backend is authoritative. The node's durable snapshot is a private
recovery cache; Xray is a derived runtime. Recovery must converge the node to
the backend's complete exact snapshot without hand-editing either copy.

## Safety rules

- Keep certificate verification enabled. Never use `curl -k`.
- Never print or paste bearer tokens, snapshot bodies, VLESS UUIDs, REALITY
  keys, or Xray generated configuration into terminals, tickets, or logs.
- Obtain credentials through the approved secret mechanism and pass them using
  protected files/environment handled by the deployment tooling, not command
  arguments.
- Do not edit snapshot JSON, partially apply accesses, delete the persistent
  volume, or use Xray/3x-ui as an authoritative writer.
- Back up and inspect only safe metadata (file existence, owner, mode, size,
  timestamps, and separately obtained expected revision/hash). Store any
  private artifact root-only and subject to the deployment retention policy.

## Crash, drift, or failed startup

1. Isolate the lagging node's **public Xray listener** from subscriber traffic
   using the reviewed load-balancer/firewall procedure. Keep SSH and the HTTPS
   management plane available only to their existing restricted sources.
2. Record safe evidence: deployed commit, container health/state, certificate
   expiry/hostname, snapshot file metadata, and the agent's authenticated health
   and snapshot metadata. Do not capture request bodies or secret-bearing
   environment/config output.
3. Ask the backend for its authoritative full exact snapshot. Do not derive a
   payload from the node cache and do not manually add, remove, reorder, or
   repair entries.
4. Send that complete backend-produced document with authenticated
   `PUT /api/v1/snapshot` over verified TLS. An overflow response means nothing
   was partially applied: correct the authority-side size/contract issue and
   retry a complete snapshot.
5. Read authenticated health and snapshot metadata. Require `READY` plus the
   exact backend revision and hash. Also confirm the backend reconcile reports
   the same metadata before restoring the public listener.
6. Restore subscriber traffic gradually and watch readiness, drift, apply,
   Xray, and transport-plane signals.

Operational diagnostic records may be access-controlled outside the repository,
but the A-010 release-evidence JSON has the narrower closed schema documented in
`RELEASE_EVIDENCE.md`. Do not copy hostnames, IPs, certificate data, snapshot
hashes, credentials, payloads, or free-form diagnostic notes into that JSON.

For a no-snapshot startup, keep the listener isolated: `NOT_READY` is expected.
Trigger a full backend reconcile; only the resulting exact PUT and matching
probe may make the node ready. For a corrupt or unsafe durable file, preserve it
privately for inspection and follow the reviewed backup/restore process in
`DEPLOY.md`; never modify content or permissions merely to pass validation.

## Diagnosis

For revision drift, compare only backend and agent revision/hash metadata, then
repeat the exact-snapshot procedure. A revision conflict indicates the same
revision carries different content; preserve evidence, keep traffic isolated,
resolve the authority-side revision assignment, and issue a newer complete
snapshot. Never force a lower revision.

For Xray timeout/unavailable/protocol events, inspect container state, the
private management network, HandlerService health, the pinned version/digest,
and bounded service logs. Do not expose port 10085 or log RPC payloads. Restart
or redeploy only through the reviewed deployment procedure, then reconcile the
authoritative snapshot before restoring traffic.

For TLS failure, inspect nginx status/config validation, certificate hostname,
chain and expiry, firewall/allowlist, and DNS. TLS terminates in the deployment
plane, so an agent event cannot diagnose a handshake that never reached it.
Keep verification enabled and do not change the API contract to work around a
transport problem.

## Authentication and rotation

An auth-failure event contains no credential. Check node identity and secret
selection without displaying either token. Rotation remains staged: deploy
current plus next, switch the backend and verify health/reconcile, then promote
next and remove old current. On failure return to the previous stage; never use
a shared fleet token or place a token in a command, health-check output, or log.
