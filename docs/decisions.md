# Implementation decisions

- **Separate policy from language.** Strands can propose an action but cannot append the money
  ledger or sign a grant. Only AgentSafe has signing permission; the executor independently
  verifies each grant's signature, scope, expiry and one-use nonce.
- **Bind identity before tools run.** Ingress resolves the sender phone to a Cognito subject.
  The Strands hook overwrites actor identity on each tool call, preventing a prompt from
  selecting another buyer or the seller.
- **Keep the payment provider explicit.** The hosted PSP is a simulation with a signed webhook.
  It proves orchestration and the authorization boundary without charging anyone.
- **Use AWS-managed channel ingress.** SNS connects WhatsApp and RCS/SMS to a shared ingress
  Lambda. RCS taps are JSON in `messageBody`; RCS photos are uploaded to a restricted S3 prefix
  using a separate service role. The first handset event still needs a live validation.
- **Reuse verified SES identities.** Email verification precedes deployment, so the CDK stack
  does not try to recreate an existing address identity.
- **Package for each runtime.** Lambda includes a current, tested SDK with RCS/AgentCore support.
  The AgentCore image contains the complete Python service package and policy file, while
  `.env`, caches and checkout metadata are excluded.
- **Preserve audit history.** Seeding is idempotent and preserves inventory. Separate buyers
  are needed for the two refund demo scenarios because policy limits refund frequency.
  Tests explicitly replace live configuration with fixture identities and mock services.
- **Let the order run itself.** Sellers don't chat. A courier's HMAC-signed pickup scan records dispatch (simulated,
  like the PSP), so the dispatch photo is the courier's, not a seller's claim. The seller is told about payment,
  Truce and settlement in one-way RCS/SMS (email if those fail), and is asked only when policy escalates.
- **Seller first, then a supervisor.** It is the seller's payout, so the seller approves escalations. A rejection or
  24 hours of silence hands the decision to a platform supervisor; a supervisor rejection returns the buyer to the
  ladder. Approval tokens carry their stage, and AgentSafe refuses a seller token once the supervisor owns it.
- **Drift escalates, it never allows.** Repeated Truce denials, inconclusive evidence (low confidence, no damage
  visible, an item that doesn't match the pickup photo), a policy change since the hold, or an off-ladder amount
  send an otherwise in-policy resolution to a human. Hard limits (no evidence, more than is held, velocity,
  identity) stay denials. A discount declined while buying doesn't count as pressure.
- **Keep both sides whole.** After a partial refund or credit, `settle` releases the rest of the hold to the seller
  as its own decision by `system:workflow`; after a replacement, the courier ships again and the buyer's delivery
  code releases the payout. A full refund pays nothing out.
- **Approval links don't act on GET.** Mail scanners fetch links. GET shows a confirmation page; only POST acts.
  Buyer text and model notes are escaped in the approval email.
- **Decionis gateway in front, reference behind, fail closed.** The hosted gateway is a proxy that evaluates each
  call, records a dossier and relays it to its tenant's upstream; it doesn't sign Clasp's authorizations. So the
  reference service stays the signer behind a token-protected `POST /decide` that serves the gateway's origin proof.
  In enforcement, an unanswered gateway means no decision: the request is denied as `decionis_unavailable` (it doesn't
  count as buyer pressure), the PSP webhook answers 503 and the checkout page lets the buyer press Pay again (the
  mock PSP doesn't redeliver), and settlement retries in Step Functions. Only `AGENTSAFE_GATEWAY_MODE=shadow` falls
  back to the reference service; in enforcement its Lambda refuses a direct decide request, so no other Clasp
  Lambda can reach an allow except through the gateway. A gateway block is final and
  recorded; a held call becomes a Clasp escalation.
- **Plain words from policy.** AgentSafe returns a `message` for the person next to the reason code, so neither the
  model nor the scripted agent has to translate `evidence.challenge_verified == true`.
