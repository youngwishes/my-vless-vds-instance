# VLESS agent deployment

Deployment is always **test first**, then a serial production rollout. These
instructions prepare commands; they do not grant approval to run them.

## Preflight and secrets

1. Review `docs/COMPATIBILITY.md`, select an exact lowercase 40-character commit
   SHA, and verify that its Compose file pins Xray 26.7.11 at the reviewed digest.
2. Copy `deploy/inventory.example.ini` to the ignored `deploy/inventory.ini` and
   create environment group vars from the `*.example.yml` files. Keep strict SSH
   host-key checking and pre-populate known host keys out of band.
3. Create the ignored `deploy/group_vars/all/vault.yml` from
   `vault.example.yml`. Generate a different current bearer token (at least 32
   characters) and REALITY private key for every node. Encrypt it with
   `ansible-vault`; never pass secret values on a command line or store the Vault
   password in the repository. Copy each environment example to its exact group
   name (`vless_test.yml` or `vless_prod.yml`) and replace documentation values.
4. Provision the TLS certificate and key out of band at the configured paths.
   The certificate must match the agent domain and remain valid beyond the
   configured safety window. The private key must be a regular non-symlink file
   with mode 0400 or 0600 and must match the certificate.
5. Confirm the central-backend IPv4 allowlist and management port. Verify the
   host has no conflicting Docker/VPC subnet and that VLESS TCP 443 and
   established SSH remain reachable. The role exposes no plaintext management
   listener: nginx provides TLS only, while Compose binds the agent to loopback.

Run syntax checks locally with the documentation-only inventory before using a
real inventory. The actual test invocation uses the ignored inventory and Vault:

```bash
ansible-playbook -i deploy/inventory.ini deploy/playbook-test.yml \
  --ask-vault-pass -e deploy_revision=<EXACT_TESTED_40_CHARACTER_SHA>
```

The health gate verifies TLS, bearer authentication, canonical contract v1 and
schema 1.0 fields, exact agent SHA, readiness, and exact Xray version/digest with
bounded retries. Canonical health has no `node_id`. Deployment identity is
instead anchored by the inventory-specific token lookup, the per-host
certificate/domain, and the verified exact checkout SHA. After test health, run
backend reconcile and verify the expected snapshot revision and hash before
considering production.

The host firewall has one narrow loopback-interface exception for management
health requests originating on the node itself. It matches `-i lo`, not a source
address range; every external interface remains restricted to the explicit
central-backend IPv4 allowlist. Ansible health requests set `use_proxy: false`,
so the host proxy is disabled and cannot receive the bearer credential or route
the local health gate away from the node.

## Snapshot backup and restore drill

Before checkout changes, the role copies an existing named-volume snapshot to a
root-only mode-0600 backup and enforces bounded retention. It never removes the
named volume. Before production, perform a restore drill on a disposable test
node: stop the test service without deleting volumes, preserve the suspect
snapshot, restore the selected backup with mode 0600, start the same compatible
SHA, then verify HTTPS health and a full backend reconcile. Never edit snapshot
JSON, delete paid state, run a volume-pruning command, or roll snapshot schema
backward.

## Token rotation

Rotation is staged per node:

1. Deploy **current + next**, keeping the current backend credential active.
2. Perform the backend switch to next, then verify HTTPS health and reconcile.
3. Promote next to current in Ansible Vault, remove the old current value, and
   deploy again.

At every stage tokens remain unique per node. A failure returns to the previous
credential stage; never fall back to a fleet-wide shared token.

## Serial production rollout and approval gate

Immediately before any production execution, stop and obtain **explicit user
approval** for the exact reviewed/test-deployed SHA and named target inventory.
This explicit user approval is a mandatory, fresh production gate.
Earlier feature, merge, test, or rollback approval is not production approval.
After approval, use `deploy/playbook-prod.yml`; it has `serial: 1`, fail-fast
behavior, and a bounded health gate after every host. Recheck firewall counters,
TLS hostname/expiry, agent evidence, backend health, and reconcile before the
play advances.

## Rollback and first install

The role records the previous exact Git SHA and Compose state. A failed config,
start, or health gate may automatically roll back only when that SHA appears in
the operator-supplied compatible list backed by `docs/COMPATIBILITY.md`. It
reuses the named snapshot volume, restores the prior SHA and environment,
restarts it, verifies authenticated HTTPS health, and still fails the play for
operator review.

A first install has no invented rollback. An absent or incompatible previous SHA
also stops with state and private backup preserved. Recovery then requires an
explicitly reviewed compatible revision or a forward fix; never delete the
snapshot or force an unreviewed schema downgrade.
