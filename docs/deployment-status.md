# Deployment status — 2026-10-25

The backend is deployed and a real SMS reply has reached the recipient. The complete phone
workflow, recording and Devpost submission still require the outstanding steps below.

| Item | Evidence / current state |
|---|---|
| Master sync | Fetched `origin/master` at `b3c79dc`. Deployment fixes continue on `codex/deployment-readiness`. |
| AWS login | Intended account confirmed; region `us-east-1`. Credentials remain local. |
| Bootstrap | `CDKToolkit` reached `CREATE_COMPLETE`. |
| Five CDK stacks | Data is `CREATE_COMPLETE`; Workflow, API, Messaging and Agent are `UPDATE_COMPLETE`. No failed stacks. |
| Bedrock | Anthropic first-use form accepted; a real Claude Sonnet 4.5 Converse request succeeded. |
| AgentCore | Runtime version 2 is `READY`. Real Strands calls retrieved catalog data through Gateway. Runtime logs use the CDK-managed KMS key and 14-day retention. |
| Ingress integration | A synthetic SNS event traversed Lambda, AgentCore, Bedrock and Gateway, then sent a real SMS reply. The user confirmed SMS receipt. Orders remained at zero. This was not a handset-originated inbound message. |
| SES | Shared sender/recipient identity is `SUCCESS`; AWS accepted the setup email. |
| SMS sandbox | Buyer and seller destination phones are both `VERIFIED`. |
| Shared pool | Active pool contains the RCS agent and Swedish `CLASP` sender ID. HELP/STOP settings match, and the two-way SNS destination is bound. |
| Dedicated SMS number | All 31 registration fields were reviewed and submitted. Swedish long-code registration version 1 is `REVIEWING`; no dedicated number has been allocated. `CLASP` currently supports one-way SMS. |
| RCS agent | Test registration is approved; the agent is active in `TESTING`. Brand assets and the user's chosen privacy/terms URLs are configured. |
| RCS media | Two-way SNS and S3 media settings validated successfully with an upload-only role restricted to `inbound/rcs/*` and the exact setup-notification key. Pool association was restored and verified. |
| RCS devices | Both RCS-specific destinations remain `UNSUPPORTED`. The available iPhone/carrier combinations have no RCS setting. SMS verification and accepting an invitation do not establish RCS eligibility. |
| SMS fallback | A synthetic RCS-shaped SNS event reached the real agent, then the router handled the RCS verification rejection and sent an SMS accepted by AWS for the seller. Logs explicitly distinguish requested RCS from sent SMS. No order was created. |
| WhatsApp | Meta rejected the number because it remains in the WhatsApp Business app. The owner plans to export chats and remove that account but has not finished. AWS phone/WABA IDs and the WhatsApp SNS binding remain pending. |
| Seed | Three items, distinct buyer/seller Cognito identities and an email-only supervisor. Optional second buyer is not seeded. |
| Hosted API | The deployed checkout endpoint returned its expected not-found page for an unknown checkout. |
| Tests | 58 tests passed, including trust boundaries, workflow behavior, packaging, identity validation, RCS normalization and SDK fallback contracts. |
| Architecture | `docs/architecture.png` exported at 2400 × 1580 and saved in the existing Devpost draft. Source and asset manifest are included. |
| Devpost story | Corrected draft saved: payment simulation is disclosed; SNS and the YAML policy service match the implementation. Existing ACE opportunity retained. |
| ACE campaign | Existing Clasp opportunity was created on 2026-10-25, but its marketing source is `None` and the required campaign is missing. AWS rejects updates while its review status is `Submitted`. Adding the campaign also requires confirmation of whether AWS MDF funding was used. |
| Full phone run | Pending WhatsApp migration and an eligible RCS device. No actual inbound RCS event has been observed. |
| Recording | No real three-phone recording or final video yet; only two phones are available. The assembly script requires real input captures. |
| Submission | Draft only. Video URL is empty. The linked GitHub repository is private; judge access must be resolved before submission. |

The requested deadline is **2026-10-28 13:00 PDT / 20:00 UTC / 21:00 Stockholm**. Carrier
review and access to an eligible RCS device are external dependencies; the AWS sender ID does
not replace the requested dedicated SMS number or an RCS handset test.

Raw receipts, contact details and logs remain in the ignored `demo/deployment-state/` directory.
Do not publish it. The live sequence and recording script are in [the runbook](../demo/RUNBOOK.md).
