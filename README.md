# Clasp

**Nothing moves until both sides are kept whole.** When something goes wrong, Clasp calls a Truce.

Clasp is an agentic commerce layer for chat marketplaces, built for the AWS Communication
Developer Services Agentic AI Partner Hackathon. The seller is paid only on
provider-confirmed funds; the buyer's refund is decided by policy, not by a persuadable model.

The agent negotiates, coordinates delivery and triages disputes across WhatsApp, RCS, SMS and
email. Every money-moving action it proposes is authorized or denied by an execution control
plane (AgentSafe) that sits between the agent and the payout ledger.

> Status: M0 skeleton and M1 trust core are done and tested offline. Workflow, agent, channels and
> infrastructure follow in the next milestones. See the end of this file for what a human must do.

## The invariant

The LLM has no authority. No agent tool writes the ledger or moves money. Money tools call
`agentsafe.decide()`. Only `services/executor` writes the ledger, and only with an authorization that is

* validly signed (compact JWS, ES256, KMS asymmetric key),
* unexpired, with a lifetime of at most 120 s,
* single-use (nonce consumed atomically with the ledger write), and
* an exact match for the request: action, order_id, amount_minor, currency, actor_sub.

`tests/test_trust_core.py` proves it: the "$1,000 refund" injection is denied with a reason; a 20 %
partial refund with verified evidence is allowed; a 120 USD refund escalates; tampered, expired,
replayed and wrong-actor authorizations are all refused and nothing is written.
`tests/test_boundary.py` checks statically that no module outside the executor can commit a money move.

## Layout

```
infra/        CDK app (one stack per concern)
services/
  agentsafe/  adapter + reference policy service + policy.yaml + KMS signer
  executor/   verifies authorization, appends ledger, advances workflow
  common/     config, stores (DynamoDB / local), logging, clock
  workflows/  Step Functions driver + local runner
  ...         ingress, agent, tools, psp_mock, approvals, notify (next milestones)
demo/         seed.py, simulate.py, RUNBOOK.md
tests/        pytest + moto
```

## Quick start (offline)

```bash
make deps      # Python 3.12 venv via uv
make test      # trust core + boundary tests
```

## Human-only steps

These cannot be scripted and must be done in the console before `make deploy` is meaningful:

1. Link the WhatsApp Business Account in the End User Messaging Social console; put the phone
   number ID in `.env` (`WHATSAPP_PHONE_NUMBER_ID`).
2. Create the RCS agent and register test devices; request an SMS origination number; put both in
   one pool (`RCS_POOL_ID`). Verify destination numbers while in the SMS sandbox.
3. Verify SES identities (`SES_FROM_EMAIL`, `SUPERVISOR_EMAIL`, demo buyer/seller emails).
4. Enable Bedrock model access for the Claude model in `BEDROCK_MODEL_ID`.
5. Create the ACE opportunity with campaign code `AWS CDS Agentic AI Hackathon -Sept. 2026`.

## License

MIT. See `LICENSE`.
