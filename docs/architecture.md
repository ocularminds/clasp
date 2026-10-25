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
```

The mock PSP records simulated holds and payouts. Actor binding, deterministic policy checks,
signed authorizations and the executor enforce the demo's money boundary. The model's evidence
assessment is a report; it does not itself authorize a refund. Runtime logs use a separate
KMS encryption key and bounded retention.
