## Inspiration

Messaging apps became storefronts, but screenshots still stand in for proof of payment and disputed photos stand in for proof of damage. Clasp explores how an agent can coordinate a purchase while every action affecting money requires a separate, signed policy decision.

## What it does

Clasp is a conversational commerce prototype. Truce is its dispute-resolution flow. The hosted checkout and payout ledger simulate payments; this demo does not charge cards, hold customer funds, or issue real refunds.

1. **Buy in the chat.** The buyer asks about inventory on WhatsApp. A Strands agent checks the catalog through AgentCore Gateway, proposes a discount within the configured 10% limit, and returns a hosted checkout link.
2. **Confirm the hold.** A signed mock-provider webhook confirms the simulated payment. The seller receives an alert through the RCS/SMS router. The two-iPhone demo uses its SMS fallback; RCS handset delivery remains untested.
3. **Record dispatch and delivery.** The seller sends the order ID and dispatch photo through WhatsApp. An SMS delivery code lets the buyer confirm receipt and authorize release through the same policy boundary.
4. **Call a Truce.** A damage report holds the payout. A fresh challenge code must appear beside the item in a new buyer photo. Bedrock evaluates the evidence; the policy service decides which compensation options are permitted.
5. **Enforce the decision.** AgentSafe checks identity, order state, available funds, amount limits, evidence and refund frequency. The executor verifies the signed authorization, exact proposal, expiry and one-use nonce before appending a ledger entry. Above-cap proposals wait for a supervisor's signed SES approval link.

Both parties can receive an SES receipt and evidence pack. A model proposal alone cannot authorize a refund. The planned three-minute recording follows one order through a supervisor-approved refund, with two phone views beside an email/console panel.

## How we built it

- **Channels:** AWS End User Messaging Social for WhatsApp, AWS End User Messaging for RCS and SMS, and Amazon SES for email. Amazon SNS delivers inbound messaging events to Lambda.
- **Agent:** Strands and Claude Sonnet 4.5 on Amazon Bedrock AgentCore Runtime, with AgentCore Gateway for tools and Memory for conversation history. Cognito and the party registry bind phone numbers to actors.
- **Authorization:** The repository's AgentSafe reference service evaluates a versioned YAML policy and signs decisions with an asymmetric AWS KMS key. Bedrock Guardrails provide an additional text filter.
- **Workflow and storage:** Step Functions coordinates the order. Separate Lambda functions evaluate policy and execute authorized actions. DynamoDB stores orders, decisions and the append-only payout ledger; S3 stores evidence. CloudWatch records diagnostic events.
- **Deployment:** Five Python CDK stacks and an AgentCore container deployment. The repository includes setup instructions, deterministic architecture exports and the recording script.

## Accomplishments and validation

The five stacks are deployed, the AgentCore runtime is ready, and a real Strands invocation used Gateway to retrieve the camera bag's inventory from the deployed catalog. A direct Bedrock Converse check also succeeded. A synthetic SNS event traversed the deployed ingress and agent, producing an SMS reply that the recipient confirmed. A separate synthetic RCS-shaped event exercised the SMS fallback. SES accepted the setup email. The RCS test registration is approved, with two-way SNS routing and S3 media storage configured; neither synthetic check establishes real inbound RCS delivery.

Automated tests cover authorization binding, policy limits, nonce replay, ledger permissions, workflow behavior, deployment packaging and inbound message normalization. As of 25 October 2026, the WhatsApp number is registered with AWS, SNS routing is bound, and its phone ID is deployed. The full two-phone workflow and recording still need a real handset run. Both available iPhones lack RCS settings, so the demo will label RCS as configured but untested.

## Challenges and lessons

The live deployment exposed details that a local simulator cannot prove: runtime packaging, permissions for user-bound agent invocation, carrier/device eligibility, and the actual RCS media envelope. RCS photos arrive as S3 references inside a JSON message body; the ingress must handle that shape before the evidence workflow can run.

Fresh challenge photos are evidence to evaluate, not a guarantee of authenticity. Keeping model reasoning separate from authorization makes the system's decision boundary inspectable and testable.

## What's next

Integrate a payment provider's delayed payouts, complete production messaging registrations, add courier proof-of-delivery integrations, and introduce WhatsApp Flows for structured dispute intake. An AWS Marketplace offering is a future distribution path.
