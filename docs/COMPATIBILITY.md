# Deployment compatibility matrix

Only a row reviewed in this repository may authorize an upgrade or automatic
rollback. An operator must copy the exact compatible SHA into
`vless_agent_compatible_rollback_revisions`; absence means stop, preserve state,
and investigate. Never infer downgrade safety from semantic versions.

| Agent contract | Snapshot schema | Xray runtime | Reviewed runtime baseline SHA | Backend expectation | Upgrade gate | Downgrade gate |
| --- | --- | --- | --- | --- | --- | --- |
| contract v1 | snapshot schema 1.0 | 26.7.11 / `sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f` | `564dc521016cc7463f7e7870ceb159b60883cccb` | backend speaks contract v1 and canonical snapshot schema 1.0 | test-first verified health and reconcile, then serial rollout | exact previous SHA declared compatible; no snapshot schema rollback |

The SHA in this first row is the reviewed **A-007 runtime baseline**, not a later
infrastructure or CI revision. A later exact SHA must receive its own review,
test deployment, complete smoke, and successful external release-evidence
validation before it may be selected as `deploy_revision` or added to the
compatible rollback list. This matrix intentionally does not claim A-010 has
been test-deployed and does not yet add its candidate SHA.

For the direct-bridge bootstrap, Docker 29.6 loopback baseline
`564dc521016cc7463f7e7870ceb159b60883cccb` is historical evidence but is not an
operational rollback target. The bootstrap changes the deployment topology to
the Compose-owned internal `172.31.255.0/28` bridge (gateway `172.31.255.1`,
Xray `172.31.255.2`, agent `172.31.255.3`) and fails closed on overlap or drift.
TLS is mandatory at external host nginx; the sole plaintext exception is nginx
and direct health to the agent on that bridge. Bootstrap evidence remains
external. No bootstrap SHA is added here until the separate final tracked commit
has passed review, CI, test deployment, and smoke.

Adding a row requires contract tests, snapshot forward/backward analysis, exact
Xray version/digest evidence, agent review, backend compatibility review, and a
test deployment. Removing or rewriting an older row does not make an incompatible
downgrade safe. Paid snapshot state is preserved; it is never deleted to force
compatibility.
