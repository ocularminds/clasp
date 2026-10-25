# Deployment status — 2026-10-26

The backend is deployed and automatic WhatsApp replies now reach the buyer. The first live
Zoom H6 order has a simulated $108 payment held. Seller dispatch, buyer evidence, the refund
workflow, recording and Devpost submission still require the outstanding steps below.

| Item | Evidence / current state |
|---|---|
| Master sync | Deployment PR #8 and two-phone demo PR #9 are merged. `origin/master` is at `6f2c250`; the Zoom H6 live-capture updates continue on `codex/live-whatsapp-capture`. |
| AWS login | Intended account confirmed; region `us-east-1`. Credentials remain local. |
| Bootstrap | `CDKToolkit` reached `CREATE_COMPLETE`. |
| Five CDK stacks | Data is `CREATE_COMPLETE`; Workflow, API, Messaging and Agent are `UPDATE_COMPLETE`. No failed stacks. |
| Bedrock | Anthropic first-use form accepted; a real Claude Sonnet 4.5 Converse request succeeded. |
| AgentCore | Runtime version 3 is `READY`, with seller instructions for WhatsApp and the order ID from the SMS alert. Real Strands calls retrieved catalog data through Gateway. Runtime logs use the CDK-managed KMS key and 14-day retention. |
| Ingress integration | A synthetic SNS event traversed Lambda, AgentCore, Bedrock and Gateway, then sent a real SMS reply. The user confirmed SMS receipt. Orders remained at zero. This was not a handset-originated inbound message. |
| Live WhatsApp integration | Real buyer messages reached SNS, ingress, Strands and Bedrock. The first run exposed incorrect boto3 method names and a removed E.164 `+` prefix. Both fixes are deployed; an automatic order-status reply is visibly received in the buyer's WhatsApp Web conversation. Photo download uses the corrected SDK operation but still needs a real image test. |
| Seller WhatsApp instructions | A direct API check against runtime version 3 asked the seller for the order ID and dispatch photo. It called no tools and orders remained at zero. This validates the agent instructions, not a handset conversation. |
| SES | Shared sender/recipient identity is `SUCCESS`; AWS accepted the setup email. |
| SMS sandbox | Buyer and seller destination phones are both `VERIFIED`. |
| Shared pool | Active pool contains the RCS agent and Swedish `CLASP` sender ID. HELP/STOP settings match, and the two-way SNS destination is bound. |
| Dedicated SMS number | All 31 registration fields were reviewed and submitted. Swedish long-code registration version 1 is `REVIEWING`; no dedicated number has been allocated. `CLASP` currently supports one-way SMS. |
| RCS agent | Test registration is approved; the agent is active in `TESTING`. Brand assets and the user's chosen privacy/terms URLs are configured. |
| RCS media | Two-way SNS and S3 media settings validated successfully with an upload-only role restricted to `inbound/rcs/*` and the exact setup-notification key. Pool association was restored and verified. |
| RCS devices | Both RCS-specific destinations remain `UNSUPPORTED`. The available iPhone/carrier combinations have no RCS setting. SMS verification and accepting an invitation do not establish RCS eligibility. |
| SMS fallback | A synthetic RCS-shaped SNS event reached the real agent, then the router handled the RCS verification rejection and sent an SMS accepted by AWS for the seller. Logs explicitly distinguish requested RCS from sent SMS. No order was created. |
| Live seller alert | After the buyer's simulated payment, the real order workflow fell back from unverified RCS to SMS. AWS accepted the seller alert; receipt on the seller's phone is not yet confirmed. |
| WhatsApp | The replacement Swedish business number is registered; AWS reports the WABA as `COMPLETE` and `Active`. AWS phone/WABA IDs are saved locally. Both topic-binding commands succeeded, and the WhatsApp topic has a confirmed ingress Lambda subscription. CDK deployment completed; ingress, tools and workflow-task functions have the real phone ID and successful updates. |
| Seed | `make seed` completed with four items, distinct buyer/seller Cognito identities and an email-only supervisor. The Zoom H6 has one unit at a simulated $120. Before the first phone test, orders and ledger entries were both zero. Optional second buyer is not seeded. |
| Hosted API | The deployed checkout endpoint returned its expected not-found page for an unknown checkout. |
| Tests | 60 tests passed, including trust boundaries, workflow behavior, packaging, identity validation, RCS normalization, SDK fallback contracts, WhatsApp SDK methods and E.164 destination preservation. |
| Architecture | `docs/architecture.png` exported at 2400 × 1580 and saved in the existing Devpost draft. Source and asset manifest are included. |
| Devpost story | Corrected draft saved: payment simulation is disclosed; SNS and the YAML policy service match the implementation. Existing ACE opportunity retained. |
| ACE campaign | Existing Clasp opportunity was created on 2026-10-25, but its marketing source is `None` and the required campaign is missing. AWS still rejects updates while its review status is `Submitted`. The user confirmed no AWS MDF funding; a complete update with `AwsFundingUsed=No` is prepared for when AWS permits edits. |
| Full phone run | The buyer created one Zoom H6 order with a 10% discount and completed simulated checkout. The order is `HELD` for $108; its ledger contains `DISCOUNT` and `HOLD`, with no payout or refund. The buyer's damage message arrived before a seller dispatch photo, so the dispute is not open. Continue the existing order with seller dispatch and real buyer evidence. No actual inbound RCS event has been observed. |
| Recording | The supplied narration was cropped, cleaned and rendered as a 177.47-second illustrated typewriter presentation. The requested revision needs real WhatsApp, SMS, email, product-photo and console captures, plus four Zoom H6 narration replacements. Its three-second opening and five-second closing are prepared. Both iPhones will use their built-in screen recorder. No real phone capture or revised live demo video exists yet. |
| Judge access | The owner-authorized invitation to `aws-cds-partner` was sent on 2026-10-25 and is pending acceptance. Personal private-repository access is write-capable. Retain access through 2026-11-19. Judges inspect the default branch. |
| Submission | Draft only. Architecture, the two-phone story, and the judge invitation link are saved; the video URL is empty. PRs #8 and #9 are on the default branch. The live-capture updates continue on a follow-up branch. |

The requested deadline is **2026-10-28 13:00 PDT / 20:00 UTC / 21:00 Stockholm**. Carrier
review remains an external dependency. The accepted two-phone demo uses the one-way sender ID
for SMS alerts; it does not establish dedicated-number provisioning or real RCS delivery.

Raw receipts, contact details and logs remain in the ignored `demo/deployment-state/` directory.
Do not publish it. The live sequence and recording script are in [the runbook](../demo/RUNBOOK.md).
