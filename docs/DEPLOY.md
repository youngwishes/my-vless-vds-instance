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
5. Confirm the central-backend IPv4 allowlist and management port. TLS is
   mandatory at external host nginx. The sole plaintext exception is host nginx
   and the deployment's direct authenticated health request to the agent on the
   Compose-owned internal bridge. The agent publishes no host port.
6. Reserve exactly `172.31.255.0/28`: gateway `172.31.255.1`, Xray
   `172.31.255.2`, and agent `172.31.255.3`. Verify that VLESS TCP 443 and
   established SSH remain reachable. The role inspects host IPv4 interfaces,
   routes, every Docker network, reserved endpoints, and the actual started
   containers. Equal, subset, or superset overlap, malformed data, foreign
   ownership, or topology drift fails closed; an adjacent subnet is allowed.
   It never deletes, recreates, or silently repairs a drifted network.
7. Remove or disable every unmanaged nginx listener out of band. The role does
   not delete operator configuration. It disables only the packaged enabled
   symlink `/etc/nginx/sites-enabled/default`, and only after proving that it is
   a symlink to `/etc/nginx/sites-available/default`; a regular file or another
   target fails closed. After installing its candidate virtual host the role
   runs `nginx -t` and inspects `nginx -T`; every effective `listen` directive
   must reduce to the single intended IPv4 TLS management listener.
8. Confirm the rendered Compose topology maps the logical `agent-snapshot`
   volume read-write to `/var/lib/vless-agent` and pins its concrete name to the
   configured Compose project. A bind, read-only mount, target drift, or volume
   name mismatch stops before replacement.

Run syntax checks locally with the documentation-only inventory before using a
real inventory. The actual test invocation uses the ignored inventory and Vault:

```bash
ansible-playbook -i deploy/inventory.ini deploy/playbook-test.yml \
  --ask-vault-pass -e deploy_revision=<EXACT_TESTED_40_CHARACTER_SHA>
```

Before nginx installation, a bounded authenticated request directly to
`http://172.31.255.3:8000/api/v1/health` proves the inspected bridge topology,
contract, schema, exact agent SHA, Xray evidence, and readiness without a host
proxy. Only then may nginx be installed, validated, and reloaded. The final
external health gate verifies TLS plus the same bearer authentication, canonical
contract v1 and schema 1.0 fields, exact agent SHA, readiness, and exact Xray
version/digest. Canonical health has no `node_id`. Deployment identity is
instead anchored by the inventory-specific token lookup, the per-host
certificate/domain, and the verified exact checkout SHA. After test health, run
backend reconcile and verify the expected snapshot revision and hash before
considering production.

After review and the complete test-node smoke, create the external closed report
described in `RELEASE_EVIDENCE.md`. Validate it against the exact current HEAD;
the candidate, CI, reviewed, test-deployed, deployed-agent, and health-agent
SHAs must be identical. The report is not stored in Git. Any tracked change
after CI, review, or test deployment invalidates that evidence and requires new
CI, review, test deployment, smoke, and report for the new SHA.

The host firewall has one narrow loopback-interface exception for management
health requests originating on the node itself. It matches `-i lo`, not a source
address range; every external interface remains restricted to the explicit
central-backend IPv4 allowlist. Ansible health requests set `use_proxy: false`,
so the host proxy is disabled and cannot receive the bearer credential or route
the local health gate away from the node.

### Management-port changes

After every successful health gate the role writes persisted root-only port
state with mode 0600. Once that state exists, any different requested port fails
before apt, firewall or nginx mutation. There is no variable override and the
state file must never be edited or deleted to bypass the gate.

A port change requires a separate reviewed implementation with dual-port
firewall/nginx choreography, central-backend routing preparation, test-first
evidence, rollback design and explicit production approval. A-008 deliberately
does not attempt this migration.

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
Successful release-evidence validation is also not production approval.
After approval, use `deploy/playbook-prod.yml`; it has `serial: 1`, fail-fast
behavior, and a bounded health gate after every host. Recheck firewall counters,
TLS hostname/expiry, agent evidence, backend health, and reconcile before the
play advances.

## Rollback and first install

Before mutation the role records the previous exact Git SHA, confirms the
previous Compose project was running, and securely captures the prior
environment, REALITY key, and nginx configuration without logging their
contents. A failed config, start, or health gate may automatically roll back
only when all those artifacts exist and the SHA appears in the operator-supplied
compatible list backed by `docs/COMPATIBILITY.md`. It restores those exact
artifacts rather than rendering current variables, validates and reloads the
restored host-wide nginx configuration, restores the prior firewall port,
reuses the named snapshot volume, restarts the prior SHA, and verifies
authenticated HTTPS health with the prior domain, port and token. The play still
fails for operator review after a successful rollback.

If compatible rollback is unavailable, explicit mutation flags drive fail-closed
cleanup. An early failure before either candidate flag does not stop the healthy
prior runtime or nginx. Once Compose may have started, the role stops all
project-labeled candidate containers without deleting volumes; once candidate
nginx may have loaded, it stops nginx. It restores each captured prior env, key
or nginx artifact that exists, otherwise removes the candidate nginx file, then
fails the play. This cleanup never checks out or starts an undeclared previous
SHA.

A first install removes the candidate nginx virtual host after failure and has
no invented rollback. An absent or incompatible previous SHA, a previously
stopped Compose project, or incomplete/unsafe prior configuration also stops
with state and private backup preserved. Recovery then requires an explicitly
reviewed compatible revision or a forward fix; never delete the snapshot or
force an unreviewed schema downgrade.

For this direct-bridge bootstrap, Docker 29.6 loopback baseline
`564dc521016cc7463f7e7870ceb159b60883cccb` is not an operational rollback
target and the role rejects it in the compatible list. The reviewed bootstrap
`fcc8f8a678638d97247a68cc6b17d3dfe0473ff2` is the compatible direct-bridge
rollback point and may be placed in that list. The external operator evidence
separately records the bootstrap SHA, a successful rollback rehearsal with
health at the bootstrap, and a successful forward redeploy with candidate
health.
