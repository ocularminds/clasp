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
