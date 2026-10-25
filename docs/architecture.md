# Architecture

Four channels feed one AgentCore agent. Every money-moving tool call passes through AgentSafe, and only a
signed authorization reaches the executor, which is the only writer of the payout ledger.

```mermaid
flowchart LR
  subgraph Channels
    WA[WhatsApp<br/>EUM Social]
    RCS[RCS + SMS fallback<br/>EUM SMS v2 pool]
    SMS[SMS<br/>delivery code]
    SES[Email<br/>Amazon SES]
  end
  WA -->|SNS| ING[ingress Lambda<br/>normalize · Cognito sub]
  RCS -->|SNS| ING
  SMS -->|SNS| ING
  ING -->|InvokeAgentRuntime<br/>session = buyer sub| RT[AgentCore Runtime<br/>Strands agent + Guardrail]
  RT <-->|MCP| GW[AgentCore Gateway]
  RT <--> MEM[AgentCore Memory]
  GW --> TOOLS[tools Lambda<br/>get_item · propose_* · issue_challenge · verify_evidence]
  TOOLS -->|decide| AS[AgentSafe<br/>policy.yaml · KMS ES256]
  AS -->|allow + signed authz| TOOLS
  TOOLS -->|request + authz| EX[executor Lambda<br/>verify · nonce · ledger]
  EX --> LEDGER[(DynamoDB ledger<br/>append-only)]
  AS --> DEC[(decisions log)]
  EX -->|SendTaskSuccess| SFN[Step Functions<br/>one execution per order]
  SFN --> TASKS[task Lambda<br/>RCS card · SMS code · SES mail]
  TASKS --> RCS
  TASKS --> SMS
  TASKS --> SES
  PSP[mock PSP<br/>hosted checkout] -->|HMAC webhook| WH[webhook Lambda] -->|propose hold| AS
  SES -->|signed approve link| APR[approvals Lambda] -->|re-decide| AS
  TOOLS --> VIS[Bedrock multimodal<br/>evidence verdict JSON]
```

`docs/architecture.png` is exported from the submission brief's diagram (TODO(human): export and commit it;
the Devpost architecture field wants the PNG).

## Trust boundary in one paragraph

The agent proposes. AgentSafe decides and signs. The executor verifies and writes. Three roles, three IAM
policies, three code paths, and the tests in `tests/` prove that none of them can do another's job.
