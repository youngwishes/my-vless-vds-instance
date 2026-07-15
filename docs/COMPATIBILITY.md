# Deployment compatibility matrix

Only a reviewed row whose downgrade gate explicitly marks it compatible may
authorize an automatic rollback. Historical rows preserve compatibility audit
history but do not authorize rollback. An operator must copy the exact
compatible SHA into
`vless_agent_compatible_rollback_revisions`; absence means stop, preserve state,
and investigate. Never infer downgrade safety from semantic versions.

| Agent contract | Snapshot schema | Xray runtime | Reviewed runtime baseline SHA | Backend expectation | Upgrade gate | Downgrade gate |
| --- | --- | --- | --- | --- | --- | --- |
| contract v1 | snapshot schema 1.0 | 26.7.11 / `sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f` | `564dc521016cc7463f7e7870ceb159b60883cccb` | backend speaks contract v1 and canonical snapshot schema 1.0 | test-first verified health and reconcile, then serial rollout | historical only; not operational direct-bridge rollback target |
| contract v1 | snapshot schema 1.0 | 26.7.11 / `sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f` | `fcc8f8a678638d97247a68cc6b17d3dfe0473ff2` | backend speaks contract v1 and canonical snapshot schema 1.0 | test-first verified health and reconcile, then serial rollout | A-010 direct-bridge bootstrap; compatible direct-bridge rollback with rehearsal and forward redeploy evidence |

The SHA in the first row remains the historical **A-007 runtime baseline**, not
a later infrastructure or CI revision. It is not a compatible direct-bridge
rollback target and must not appear in the direct-bridge compatible rollback
list. The second row is the reviewed
**A-010 direct-bridge bootstrap** and is the compatible direct-bridge rollback
point. Its successful rollback rehearsal and subsequent forward redeploy to the
candidate are recorded in external release evidence.

For the direct-bridge bootstrap, Docker 29.6 loopback baseline
`564dc521016cc7463f7e7870ceb159b60883cccb` is historical evidence but is not an
operational rollback target. The bootstrap changes the deployment topology to
the Compose-owned internal `172.31.255.0/28` bridge (gateway `172.31.255.1`,
Xray `172.31.255.2`, agent `172.31.255.3`) and fails closed on overlap or drift.
TLS is mandatory at external host nginx; the sole plaintext exception is nginx
and direct health to the agent on that bridge. Bootstrap evidence remains
external and contains only the closed safe status vocabulary.

Adding a row requires contract tests, snapshot forward/backward analysis, exact
Xray version/digest evidence, agent review, backend compatibility review, and a
test deployment. Removing or rewriting an older row does not make an incompatible
downgrade safe. Paid snapshot state is preserved; it is never deleted to force
compatibility.
