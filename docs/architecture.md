# Architecture

![Clasp AWS architecture](architecture.png)

The export reflects the CDK implementation. `demo/export_assets.py` reproduces the PNG.

```mermaid
flowchart LR
  Buyer[Buyer: WhatsApp] --> Social[AWS End User Messaging Social]
  Seller[Seller: one-way RCS / SMS] -.-> RCS[AWS End User Messaging SMS / RCS]
  Social --> SNS[SNS inbound topics]
  RCS --> SNS
  RCS -->|Inbound photos, upload-only role| S3[S3 media]
  SNS --> Ingress[Ingress Lambda]
  Cognito[Cognito verified phone identities] --> Ingress
  Ingress --> Runtime[AgentCore Runtime: Strands]
  Runtime --> Claude[Bedrock Claude + PII Guardrail]
  Runtime <--> Memory[AgentCore Memory]
  Runtime --> Gateway[AgentCore Gateway: MCP + Cognito JWT]
  Gateway --> Tools[Tools Lambda]
  Tools -->|Proposals| Decionis[Decionis AgentSafe hosted gateway]
  Decionis -->|relays with bearer token| Policy[AgentSafe policy Lambda: POST /decide]
  Tools -.->|shadow mode only: fallback| Policy
  Checkout[HTTP API: mock PSP checkout] --> Policy
  Courier[HTTP API: mock courier pickup scan] -->|signed pickup| Workflow
  Approvals[HTTP API: approval confirm pages] --> Policy
  Policy -->|ES256 authorization| Executor[Executor Lambda]
  KMS[KMS signing key] --> Policy
  Executor -->|Sole ledger writer| DDB[DynamoDB]
  Tools --> Workflow[Step Functions]
  Executor --> Workflow
  Workflow -->|settle the rest to the seller| Policy
  Workflow --> Notify[Task Lambda / notifications]
  Notify --> Social
  Notify --> RCS
  Notify --> SMS[SMS delivery code, WhatsApp fallback]
  Notify --> SES[SES receipts / seller then supervisor approvals / evidence]
  S3 --> Tools
  Tools -->|explain_policy: Retrieve| KB[Bedrock Knowledge Base: compensation policy on S3 Vectors]
  PolicyDocs[S3 policy documents, rendered from policy.yaml] --> KB
  Notify -->|order lifecycle events| Bus[EventBridge bus clasp-dev]
  Tools --> Bus
  Bus --> EventLog[CloudWatch Logs audit trail + 7-day archive]
  Bus -->|courier booked / dispatch / code / release| DeliveryTopic[SNS delivery events: courier integration point]
```

The Knowledge Base holds the plain-language policy the agent quotes from (`explain_policy`); it is generated from the
same `policy.yaml` that AgentSafe enforces, so the words cannot drift from the numbers. Lifecycle events are
observability and integration hooks, published by the workflow task bodies; they are never on the money path.

The order runs without anyone chatting with the seller: the PSP webhook holds the payment, the courier's
HMAC-signed pickup scan records dispatch, the buyer's code releases the payout, and in-policy Truce outcomes
settle automatically, with the rest of the hold released to the seller as its own AgentSafe decision. Policy
escalations (above the automatic cap, or drift: repeated denials, inconclusive evidence, a policy change, an
off-ladder amount) go to the seller by signed SES link, then to a platform supervisor if the seller rejects or
doesn't answer within 24 hours. Approval links open a confirmation page; only the POST acts.

Every decision passes through the Decionis AgentSafe hosted gateway when `AGENTSAFE_URL` is set. In enforcement it
can block or hold a call; otherwise it records a dossier and relays the call to the token-protected `POST /decide`,
where the reference service decides and signs. If the gateway can't answer, nothing moves: the request is denied,
and payment holds and settlement are retried. Only shadow mode falls back to the reference service.

The mock PSP and courier record simulated holds, pickups and payouts. Actor binding, deterministic policy checks,
signed authorizations and the executor enforce the demo's money boundary. The model's evidence
assessment is a report; it does not itself authorize a refund. Runtime logs use a separate
KMS encryption key and bounded retention.
