# Architecture

![Clasp AWS architecture](architecture.png)

The export reflects the CDK implementation. `demo/export_assets.py` reproduces the PNG.

```mermaid
flowchart LR
  Buyer[Buyer: WhatsApp] --> Social[AWS End User Messaging Social]
  Seller[Seller: RCS] --> RCS[AWS End User Messaging SMS / RCS]
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
  Tools -->|Proposals| Policy[AgentSafe policy Lambda]
  Checkout[HTTP API: mock PSP checkout / approval links] --> Policy
  Policy -->|ES256 authorization| Executor[Executor Lambda]
  KMS[KMS signing key] --> Policy
  Executor -->|Sole ledger writer| DDB[DynamoDB]
  Tools --> Workflow[Step Functions]
  Executor --> Workflow
  Workflow --> Notify[Task Lambda / notifications]
  Notify --> Social
  Notify --> RCS
  Notify --> SMS[SMS delivery code]
  Notify --> SES[SES receipts / approvals / evidence]
  S3 --> Tools
  Tools -->|explain_policy: Retrieve| KB[Bedrock Knowledge Base: compensation policy on S3 Vectors]
  PolicyDocs[S3 policy documents, rendered from policy.yaml] --> KB
  Notify -->|order lifecycle events| Bus[EventBridge bus clasp-dev]
  Tools --> Bus
  Bus --> EventLog[CloudWatch Logs audit trail + 7-day archive]
  Bus -->|dispatch / code / release| DeliveryTopic[SNS delivery events: courier integration point]
```

The Knowledge Base holds the plain-language policy the agent quotes from (`explain_policy`); it is generated from the
same `policy.yaml` that AgentSafe enforces, so the words cannot drift from the numbers. Lifecycle events are
observability and integration hooks, published by the workflow task bodies; they are never on the money path.

The mock PSP records simulated holds and payouts. Actor binding, deterministic policy checks,
signed authorizations and the executor enforce the demo's money boundary. The model's evidence
assessment is a report; it does not itself authorize a refund. Runtime logs use a separate
KMS encryption key and bounded retention.
