# Design decisions

Short records of the choices that shaped Clasp, in the order they were made.

## 1. The model has no authority; the executor has no judgement
Every money tool is a *proposal*. AgentSafe (policy) decides; the executor only checks a signature and writes.
Neither component can do the other's job, so neither can be talked into it. Tests, not prose, hold this:
`tests/test_trust_core.py`, `tests/test_boundary.py`, `tests/test_infra_synth.py` (IAM).

## 2. Authorizations are compact JWS, ES256, KMS-signed, ≤120 s, single-use, exact-match
A signed token can be logged, carried through Step Functions, and verified without a shared secret. KMS keeps
the private key out of every process. The executor verifies through `kms:Verify`, so Lambda needs no crypto
library. The nonce is consumed in the same DynamoDB transaction as the ledger write, so a replay cannot even
race. Exact-match means a token for $21.60 is useless for $21.61.

## 3. Identity comes from the channel, not the conversation
Ingress resolves the sender's phone to a Cognito `sub`. A Strands hook writes that `sub` into every tool call,
over whatever the model produced. AgentSafe then requires `actor_sub == order.buyer_sub`. Prompt injection can
change what is asked for, never who is asking.

## 4. Evidence lives in the store
`propose_refund` looks up the verified challenge itself. The model's claim that evidence was verified is just text.

## 5. Policy as data
`policy.yaml` is versioned, loaded at cold start, and its version is a claim in every authorization and a field
in every decision record. Demo numbers (10 % discount, $50 / 30 % auto cap, 1 refund per 30 days) are knobs.

## 6. One state machine per order, task tokens for every human step
Payment, dispatch, delivery, dispute and approval are `waitForTaskToken` states. The token is stored on the order,
and the executor completes the right wait after a money move. The same task bodies run under Step Functions and
under the in-process `LocalDriver`, so the console graph and the offline simulation cannot drift apart silently
(the synth test checks the state names).

## 7. Webhook, not screenshot
The HOLD is proposed by the webhook receiver as `system:psp`, with `psp_verified` evidence, only after the HMAC
checks out. AgentSafe still decides; the executor still writes. The seller's RCS card says "Funds secured by
provider" because that is literally what happened.

## 8. Escalation is a signed artifact too
The supervisor's email links are JWS tokens bound to one order, one amount and one `escalate` decision. The handler
verifies the signature before reading a claim; AgentSafe verifies it again; an `ESCALATED` order moves nothing
without it. A link with an edited amount yields 400 and no write (tested).

## 9. Guardrails mask; they do not defend
The Bedrock Guardrail anonymizes card and account numbers on agent input and output. It is presented as hygiene.
The defense against "refund me $1,000" is AgentSafe, and the demo shows the model *losing* that argument.

## 10. Four stacks of boundaries in IAM
Only the executor role has `dynamodb:PutItem` on the ledger (and nobody has `DeleteItem`). Only AgentSafe has
`kms:Sign`. The tools role, which is what Gateway reaches, can only *invoke* those two functions.

## Deferred
Stripe test mode behind `PSP=stripe`; WhatsApp Flows for structured dispute intake; courier proof-of-delivery;
a Knowledge Base for policy Q&A. Cut order if time runs short (per the brief): Guardrails → Cognito (phone-to-order
matching inside AgentSafe) → visual comparison (keep the code check). Never AgentSafe or the executor checks.
