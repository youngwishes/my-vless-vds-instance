# Deployment compatibility matrix

Only a row reviewed in this repository may authorize an upgrade or automatic
rollback. An operator must copy the exact compatible SHA into
`vless_agent_compatible_rollback_revisions`; absence means stop, preserve state,
and investigate. Never infer downgrade safety from semantic versions.

| Agent contract | Snapshot schema | Xray runtime | Reviewed runtime baseline SHA | Backend expectation | Upgrade gate | Downgrade gate |
| --- | --- | --- | --- | --- | --- | --- |
| contract v1 | snapshot schema 1.0 | 26.7.11 / `sha256:a1644183accdb0b5be967093fe34be756fd5de15fe2ee0206e842ae17350967f` | `564dc521016cc7463f7e7870ceb159b60883cccb` | backend speaks contract v1 and canonical snapshot schema 1.0 | test-first verified health and reconcile, then serial rollout | exact previous SHA declared compatible; no snapshot schema rollback |

The SHA in this first row is the reviewed **A-007 runtime baseline**, not the
final A-008 infrastructure revision. The final A-008 exact SHA must receive its
own review and test deployment evidence before it may be selected as
`deploy_revision` or added to the compatible rollback list.

Adding a row requires contract tests, snapshot forward/backward analysis, exact
Xray version/digest evidence, agent review, backend compatibility review, and a
test deployment. Removing or rewriting an older row does not make an incompatible
downgrade safe. Paid snapshot state is preserved; it is never deleted to force
compatibility.
