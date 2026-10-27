# Hosted gateway and Clasp policy verification — 2026-10-27

The hosted Clasp gateway runs **AgentSafe 0.7.0** in ENFORCEMENT. Decionis's native API published the
named policy **`clasp`**, and one synthetic $1 payment passed through that policy and produced exactly
one HOLD. The dossier's policy snapshot, execution binding and three Ed25519 signatures were verified.

This checkpoint covers the gateway and a synthetic payment hold. The two recorded WhatsApp Truce
takes used the earlier flow; a complete courier-to-Truce-to-settlement handset run on the current
autonomous flow remains unverified. Payments are simulated.

## Deployment

- Clasp source baseline: `1cfd647e01d6684d7a48017b11d12458c00c9f67` (through PR #19).
- The deployment check completed with all seven CDK stacks already current. At 08:57 UTC, all eight
  service Lambdas were Active/Successful, and AgentCore runtime 5 and its DEFAULT endpoint were READY.
- Hosted gateway: `https://clasp.decionisedge.com`, tenant `clasp`, authority `decionis`.
- Both replicas of deployment `agentsafe-tenant-clasp` report runtime `0.7.0`, ready, ENFORCEMENT and
  verified upstream proof. `make gateway-check` passed readiness and the authenticated origin-proof relay.
- The software rollout changed only this deployment's image, using the published immutable runtime
  digest `sha256:041551b32ae95493c2c38934965621411ef387a4aa2744159821aab1ab13da4b` in the existing registry.
  The shared gateway host and provisioner images were not changed.
- Release: [AgentSafe v0.5.0](https://github.com/decionis/agent-safe-pipeline/releases/tag/v0.5.0),
  containing runtime **0.7.0** and library **0.5.0**. Package, container and Helm provenance checks passed.

The existing `EnforcedTenant.sh` renderer derives its image from the shared host. Future regeneration
of this tenant's manifest must preserve the verified 0.7.0 digest until that fleet setting is updated.

## Published policy and binding

| Field | Verified value |
| --- | --- |
| Policy name | `clasp` |
| Bundle ID | `e6078687-55e5-4323-98bc-7f2edc9b9b88` |
| Version | `clasp-gateway-2026.10.27-1` |
| State | `ACTIVE` |
| Published | 2026-10-27 17:28:07 UTC |
| Selected rule | `clasp-decide-allow` |
| Rule action | `AUTO_APPROVE` |
| Request match | `http.post`, exact path `/decide`, and a known Clasp money action |
| Policy selection | Named policy bound to the gateway's workspace credential |

`POST /v1/policies/validate` accepted the native bundle. Publication through
`POST /v1/protocol/policies/bundles` returned HTTP 202, and a subsequent bundle-list read returned
the exact payload as ACTIVE. The rule in [the runbook](../demo/RUNBOOK.md) passed 19 native-core cases,
including all seven money actions, nonmatching requests returning review, and a higher-priority deny.

The managed deployment flow retained the existing upstream Authorization credential and associated
policy name `clasp`. At 17:38:18 UTC, the mounted workspace credentials on both replicas reported
`policy_id: clasp` and scope `decision:enforce`. **This binding selects the newest active version of
`clasp`; it is not an immutable route pin.** The provisioner owns the tenant registry, so no manual
route override was used. The action remains `http.post` and the rule requires path `/decide`.

The separate governed authoring draft remains revision 3 and was not released. Its earlier
`POLICY_ENCODING_INVALID` and evidence-custody errors describe that draft. The native API bundle
above is the active runtime policy. No existing commerce policy was replaced.

The gateway rule admits the request to Clasp's policy service. Clasp still validates amount,
identity, state, evidence, velocity and approval requirements before signing the money authorization.

## Live hold

Pay was sent **once**, after active publication and both credential bindings were verified. It
returned **HTTP 200, Payment confirmed**, at 17:40:51 UTC. The AWS read at 17:41:30 UTC confirmed:

| Evidence | Result |
| --- | --- |
| Order | `ord_smoke_20261027_6a2922e3dddb` |
| Checkout | `chk_smoke_20261027_6a2922e3dddb` |
| State | `HELD`, USD 1.00 held; released, refunded and credited amounts all zero |
| Ledger | Exactly one entry, `HOLD`, sequence 1 |
| Ledger entry | `led_6ae0e223ce2766e96922` |
| Clasp decision | `dec_6ae0e21f1aa370f74877`, `allow`, reason `in_policy`, stored engine `decionis-gateway` |
| Decionis evaluation | `8f98f4a5-a81b-4401-ab33-9c3300b099f7`, `ALLOW` / `APPROVE` |
| Dossier | `f7f1f55e-cfa5-4b59-a266-04f82b1cfcca` |
| Selected policy | Both the snapshot and execution binding identify the bundle and version above |

The decision diagnostic printed:

```text
17:40:47 allow    decionis  hold             $1.00  22e3dddb  actor=system:psp     in_policy  dossier=f7f1f55e
```

The reference service's own policy version is `2026.10.27-1`; the selected Decionis bundle version
is `clasp-gateway-2026.10.27-1`. They describe different policy layers.

## Signed evidence

At 17:48:53 UTC, the saved dossier's portable artifact, input snapshot and execution binding all
passed digest recomputation and Ed25519 signature verification against key
`decionis-dossier-prod-2026-09-27`, retrieved from Decionis's HTTPS
[public JWKS](https://api.decionis.com/.well-known/decision-dossier-jwks.json).

The published rules and parameter hashes also match the policy snapshot. The execution policy digest
is `sha256:6518db12d5fb47fbcf76f45b726feac84bc15b2035ef4e25b72551c1868aea7c`.
This verifies the historical signed artifacts and policy selection; a complete Decionis ledger-chain
verification was not performed.

The two earlier review decisions remain as history. Do not press Pay again on this completed fixture
to repeat the check. No real demo order or phone owner's refund allowance was reset.

Raw account responses, dossier payloads and local verification receipts remain in the ignored
`demo/deployment-state/` directory. Credentials stay in the configured secret stores and local `.env`.
This report contains only the scoped deployment findings and synthetic test identifiers needed to
locate the evidence.
