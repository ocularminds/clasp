# Deployment status — 2026-10-27 (autonomous flow deployed; gateway hold proven; live take on the earlier flow)

Two live Zoom H6 orders have completed the Truce flow with a $21.60 simulated partial refund
each. The selected second take used real WhatsApp dispatch and challenge photos, an AgentSafe
`allow` decision, exactly one refund entry, a successful Step Functions execution, and two
received SES evidence-pack emails. The model matched the challenge code and item; it did not
confirm the reported damage. Payments remain simulated. The ACE campaign
association, judge fixture reconciliation and Devpost submission remain open. The completed live video is
[published unlisted](https://youtu.be/R1EKxswOsns); the verified export is 1:53.933. The two available iPhones are unsupported for the RCS test.

**Deployed on 27 October: the autonomous flow (PR #16, `d33a79c`).** It adds:

- courier pickup scans in place of seller WhatsApp dispatch;
- seller-then-supervisor approval;
- policy drift escalation and settlement of the remainder to the seller;
- the Decionis AgentSafe hosted gateway in enforcement, failing closed (the reference service decides directly only in shadow mode);
- short-video evidence, workflow notices in the agent's context, email capture and delivery-code resend;
- the copy rewrites.

**Current application deployment: the re-record fixes (PR #17, `2fb41d5`).** At the **08:57 UTC** check on
27 October, all seven stacks were `UPDATE_COMPLETE`, all eight service Lambdas were Active/Successful, and
AgentCore runtime 5 and its DEFAULT endpoint were READY. The deployment check used source baseline `1cfd647`.
PRs #18, #19 and #21 change only documentation and the local `make decisions` script; they require no further
application deployment.
Decision callers use `AGENTSAFE_URL=https://clasp.decionisedge.com` and `AGENTSAFE_GATEWAY_MODE=enforcement`;
the deployed `COURIER_MODE` is `auto`.

**Hosted gateway check (17:03 UTC):** both Clasp replicas report AgentSafe **0.7.0**, ready, ENFORCEMENT,
authority `decionis` and verified upstream proof. `make gateway-check` passed readiness and the authenticated
origin-proof relay.
The earlier origin check also confirmed that Clasp's `POST /decide` returns 401 without the upstream token.

**The gateway path is proven with one live hold (27 October, 17:40 UTC).**

- **Policy:** the `clasp` tenant's policy is the named Decionis policy `clasp`, version `clasp-gateway-2026.10.27-1`
  (bundle `e6078687-55e5-4323-98bc-7f2edc9b9b88`), published through the Decionis API and read back as `ACTIVE`. Its one
  rule, `clasp-decide-allow`, answers `AUTO_APPROVE` for `http.post` on the exact path `/decide` when the body's
  `action` is one of Clasp's seven money actions. The runbook has the rule.
- **Gateway:** it runs AgentSafe 0.7.0 in enforcement. Its workspace credential is bound to the policy name `clasp`,
  and it evaluates the newest `ACTIVE` version of that policy. That is not an immutable pin: publishing a new version
  changes the live rule.
- **Before the policy:** the first two attempts on the synthetic $1 checkout (`ord_smoke_20261027_6a2922e3dddb`) came
  back `decionis_escalate: PROTOCOL_OUTCOME_REVIEW,POLICY_MISMATCH`. The order stayed unpaid with no ledger entry,
  which is the fail-closed behaviour. Their Decionis reviews, dossiers `77e1acf9…` and `b1399668…`, remain as
  history.
- **After publication:** one Pay on the same checkout returned HTTP 200 at 17:40:51 UTC.
  - The gateway answered `allow` and forwarded the call (`execution=FORWARDED`). That proves the `upstreamCredential`,
    which `make gateway-check` can't.
  - Clasp's reference policy then allowed it as decision `dec_6ae0e21f1aa370f74877`, `in_policy`, with all six checks
    passing, and signed the authorization.
  - The executor wrote exactly one ledger entry, `HOLD` $1.00 (`led_6ae0e223ce2766e96922`).
  - The order is `HELD`.
  - `make decisions` shows `17:40:47 allow    decionis  hold  $1.00 … in_policy  dossier=f7f1f55e`.
- **Dossier:** Decionis evaluation `8f98f4a5-a81b-4401-ab33-9c3300b099f7` matched `clasp-decide-allow`. Dossier
  `f7f1f55e-cfa5-4b59-a266-04f82b1cfcca` names the published bundle and version in both its policy snapshot and its
  execution binding. Its three Ed25519 signatures (portable artifact, input snapshot, execution binding) verify
  against Decionis's published key `decionis-dossier-prod-2026-09-27`. The full Decionis ledger chain was not verified.

The synthetic order has no contacts and is left `HELD`; it started no Step Functions execution. No order workflow has
run on the autonomous flow yet: the last execution started at 00:07 UTC on 27 October. Nothing in the table below was
re-verified against the new flow unless the row says so.

See the [verification record](gateway-verification-2026-10-27.md) for the immutable runtime image, API publication
checks, policy binding and signed-artifact verification details.

| Item | Evidence / current state |
|---|---|
| Master sync | PRs #8–#19 and #21 are merged. The deployment check used source baseline `1cfd647`; the deployed service is `2fb41d5` (PR #17), which includes the autonomous flow from PR #16 (see above). The notification changes from PR #15 (`2ea529b`) are deployed. The earlier Knowledge Base, Events and runtime deployment came from `faf3c90`. |
| AWS login | Intended account confirmed; region `us-east-1`. The fresh `aws login` landed in the `default` profile, and the profile named in `.env` was expired, so every `make` call ran with `AWS_PROFILE=default`. That identity is the account root user; a named IAM identity would be better hygiene before judging. Credentials remain local. |
| Bootstrap | `CDKToolkit` reached `CREATE_COMPLETE`. |
| Seven CDK stacks | At the 27 October deployment check, all seven stacks were `UPDATE_COMPLETE`; `make deploy AWS_PROFILE=default` succeeded with no changes. All eight service Lambdas were Active/Successful at 08:57 UTC. The earlier notification and Knowledge Base/EventBridge deployments remain part of that stack state. |
| Knowledge Base | `ClaspDevKnowledge`: Bedrock Knowledge Base `ACTIVE` on `S3_VECTORS` with `amazon.titan-embed-text-v2:0`. `make kb-sync` rendered the policy document from `policy.yaml`, uploaded it with the Truce FAQ, and ingestion job `EJCJF4DK2Q` reached `COMPLETE` (scanned 2, indexed 2, failed 0). A direct retrieval returned the supervisor-approval passage at score 0.71. The deployed tools Lambda answered `explain_policy` with `source: knowledge_base`, proving the SSM lookup and the `bedrock:Retrieve` grant. The id is published at `/clasp/dev/knowledge_base_id`. |
| Events | `ClaspDevEvents`: bus `clasp-dev`, both rules and the 7-day archive are enabled. Real order `27e389` emitted `OrderCreated`, `OrderHeld`, `OrderDispatched`, `DeliveryCodeSent`, `TruceOpened` and `OrderResolved` into the CloudWatch audit log. The resolution event reports three decisions, two evidence packs and a 2160-minor-unit refund. `DeliveryCodeSent` means AWS accepted the send; it does not establish handset receipt. The earlier synthetic smoke event remains clearly labelled. |
| Judge enrolment | The ingress Lambda carries `AUTO_ENROL_CHANNELS=whatsapp` and `AGENT_MODE=remote`. IAM simulation for its role returned `allowed` for `cognito-idp:AdminCreateUser` on the user pool and `dynamodb:PutItem` on the parties table. An unknown WhatsApp number will be enrolled as a buyer on first contact with its own Cognito identity; SMS and RCS unknown senders still receive the "not registered" reply. Not yet exercised from a real unknown handset. |
| Seller short codes | `get_order` and `my_orders` accept the 6-character code within the caller's own orders through `by_seller`. PR #16 removed `record_dispatch`: a courier pickup scan now records dispatch. The live WhatsApp dispatch flow resolved `27e389` with `get_order` and recorded the seller's real photo. The single-awaiting-order shortcut and ambiguous matches are covered by tests. |
| Bedrock | Anthropic first-use form accepted; a real Claude Sonnet 4.5 Converse request succeeded. |
| AgentCore | Runtime version 5 and its DEFAULT endpoint were `READY` at 08:57 UTC on 27 October with the PR #17 deploy; a live query called `explain_policy` and returned the seller-first / supervisor review rules. Earlier: runtime version 4 was `READY` (21:10 UTC) with the deterministic-proposal prompt (the agent always submits a requested refund amount and relays AgentSafe's decision) and the policy-question guidance. A direct invocation as a synthetic buyer asked for the refund limit and evidence rules; the runtime called `explain_policy` through Gateway and replied with $50.00, 30% of the order, one live photo with the challenge code, and supervisor review above that. The Gateway target is `READY` and advertises all 19 tools including `explain_policy`. Runtime logs use the CDK-managed KMS key and 14-day retention. |
| Ingress integration | A synthetic SNS event traversed Lambda, AgentCore, Bedrock and Gateway, then sent a real SMS reply. The user confirmed SMS receipt. Orders remained at zero. This was not a handset-originated inbound message. |
| Live WhatsApp integration | Real messages from both demo identities traversed SNS, ingress, AgentCore and Bedrock. The second take's dispatch JPEG reached S3 at 23:17:42 UTC; the order entered `DISPATCHED`. A buyer damage report opened the Truce at 23:25 UTC. The fresh photo was verified at 23:27:09 UTC with `code_visible`, `code_matches` and `same_item_as_dispatch` true, confidence 0.95 and `damage_observed` false. Earlier SDK method, E.164 and exact S3 object-key fixes are deployed. |
| Seller WhatsApp client | The original seller's iPhone showed “Invite” despite a registered business number. An owner-authorized temporary WhatsApp Web link and app restart restored typing; that temporary linked session was then logged out. After the role swap, the new seller's received recording still shows the “no longer on WhatsApp” footer while displaying dispatch and Truce messages. The backend accepted the real dispatch; the recording does not prove that this second iPhone's compose box works. |
| SES | Production access is **GRANTED**: 50,000 messages per 24 h at 14 per second, enforcement `HEALTHY`. Both evidence-pack emails for order `27e389` arrived in the shared demo mailbox's Spam folder at 23:27 UTC. The actual JSON attachment was downloaded and checked against the order, decisions and ledger. An above-cap supervisor approval was not exercised in the selected take. |
| SMS sandbox | **Verified 27 October, 08:20 UTC, `us-east-1`: `DescribeAccountAttributes` returned `SANDBOX`; `DescribeSpendLimits` returned $1 for both the enforced and maximum monthly text-message limits.** The owner-reported production approval and $25 ceiling have not yet appeared in the service configuration. Both demo destinations were previously verified. The production-access request and user-approved $25/month ceiling were filed in AWS Support case `179305047100524`; its last console check showed `Unassigned`. The Support API requires a Premium Support subscription, so the case status was not refreshed in this check. Earlier evidence from 26 October: reported spend was $0.94328 at 23:20 UTC; the buyer's delivery-code SMS for `27e389` was accepted by AWS at 23:17:52 UTC but was still missing after the user checked the phone. No configuration set provided a delivery receipt. A follow-up with the message ID and quota evidence was submitted at 23:26 UTC; no retry was sent. |
| Shared pool | Active pool contains the RCS agent and Swedish `CLASP` sender ID. HELP/STOP settings match, and the two-way SNS destination is bound. |
| Dedicated SMS number | All 31 registration fields were reviewed and submitted. Swedish long-code registration version 1 is `REVIEWING`; no dedicated number has been allocated. `CLASP` currently supports one-way SMS. |
| RCS agent | Test registration is approved; the agent is active in `TESTING`. Brand assets and the user's chosen privacy/terms URLs are configured. |
| RCS media | Two-way SNS and S3 media settings validated successfully with an upload-only role restricted to `inbound/rcs/*` and the exact setup-notification key. Pool association was restored and verified. |
| RCS devices | Both RCS-specific destinations remain `UNSUPPORTED`. The available iPhone/carrier combinations have no RCS setting. SMS verification and accepting an invitation do not establish RCS eligibility. |
| SMS fallback | A synthetic RCS-shaped SNS event reached the real agent, then the router handled the RCS verification rejection and sent an SMS accepted by AWS for the seller. Logs explicitly distinguish requested RCS from sent SMS. No order was created. |
| Live seller alert | The user confirmed the seller's SMS hold alert in both takes. The deployed notification change keeps the hold alert within one GSM segment and includes the 6-character order code. After the second take's recent WhatsApp dispatch photo, the Truce notice used WhatsApp and was visible in the seller recording. Other cases retain the RCS/SMS route. |
| WhatsApp | The replacement Swedish business number is registered; AWS reports the WABA as `COMPLETE` and `Active`. AWS phone/WABA IDs are saved locally. Both topic-binding commands succeeded, and the WhatsApp topic has a confirmed ingress Lambda subscription. CDK deployment completed; ingress, tools and workflow-task functions have the real phone ID and successful updates. |
| Seed | Four items and two distinct phone identities were seeded. For the second take, the owner authorized swapping buyer/seller roles and restocking the Zoom H6; both original and new order histories were preserved. The Zoom H6 is now out of stock. Both identities have used their one-refund allowance for 30 days. Local `.env` still names the original roles, so judge fixtures need reconciliation before another seeded run. No refund allowance or audit record was reset. |
| Hosted API | Both recorded live takes completed a simulated $108 checkout; the signed mock-provider confirmation advanced each order to `HELD`. At 17:40:51 UTC on 27 October, the separate synthetic $1 checkout returned HTTP 200 after one Pay attempt through the hosted gateway. A fresh account read confirmed HELD and exactly one HOLD ledger entry. The signed dossier identifies the active Clasp bundle; [verification record](gateway-verification-2026-10-27.md). |
| Hosted AgentSafe | Both Clasp gateway replicas run runtime **0.7.0** in ENFORCEMENT with authority `decionis` and verified upstream proof. The published runtime image is deployed by immutable digest; `make gateway-check` passed after rollout. The shared host and provisioner images were not changed. |
| Decionis policy | Native API publication accepted **`clasp-gateway-2026.10.27-1`**, bundle `e6078687-55e5-4323-98bc-7f2edc9b9b88`, and read-back confirms ACTIVE. Both mounted workspace credentials select policy name `clasp`; its newest active version applies. Dossier `f7f1f55e-cfa5-4b59-a266-04f82b1cfcca` matches rule `clasp-decide-allow` for `http.post` and `/decide`, records ALLOW and identifies that bundle in both policy snapshot and execution binding. All three artifact signatures verify. The earlier governed draft remains revision 3 and was not released. |
| Tests | 120 tests pass on the re-record fixes (113 on `d33a79c`). Earlier: 84 tests passed on `2ea529b`, including the previous authorization, workflow, packaging, identity, SDK, media, enrolment, short-code, knowledge retrieval, event and seven-stack IAM checks. Five additional cases cover the shorter seller alert and recent WhatsApp dispatch routing, including the RCS-photo exclusion and stale/future timestamps. |
| Architecture | `docs/architecture.png` exported at 2400 × 1580 and saved in the existing Devpost draft. Source and asset manifest are included. |
| Devpost story | The corrected story is saved in the Devpost draft and was reopened to verify persistence. It records the completed partial refund, real lifecycle events, received SES evidence packs, the model's actual photo verdict, missing buyer SMS and unsupported RCS handsets. Payments are disclosed as simulated; no supervisor approval or policy denial is claimed for this take. |
| ACE campaign | Opportunity `O30313007` was created on 2026-10-25, but its marketing source remains `None` and the required “AWS CDS Agentic AI Hackathon -Sept. 2026” campaign is missing. Direct edits are locked while AWS reviews the submitted opportunity. The owner-authorized Partner Central Support request to add the campaign or enable edits was sent; its last observed status was `New`. `AwsFundingUsed=No` is confirmed. A pending support case does not satisfy the campaign requirement. |
| Full phone run | Both orders finished `RESOLVED_PARTIAL_REFUND`. The selected take, `27e389`, bought the $120 Zoom H6 with a 10% discount, held $108, recorded dispatch, opened a Truce and verified the fresh challenge photo. The buyer chose 20%, producing one $21.60 simulated refund, no release, and a successful execution at 23:27:50 UTC. Its ledger has exactly `DISCOUNT`, `HOLD` and `REFUND`; all three decisions are `allow`. No $1,000 denial or supervisor approval occurred in this take. |
| Recording | The owner selected the completed partial-refund take and corrected the three seller-labelled short exports to buyer footage. The buyer checkout/challenge/refund clips and a separate 4:47 seller dispatch-reply/Truce recording are preserved. The final 1:53.933 video uses these real captures, actual post-completion email/console excerpts, cuts of the original narrator, a 3-second Clasp opening and 5-second closing, and burned captions. The revised version 2 uses larger chat crops and labelled transcript panels. It is 1920 × 1080 at 30 fps, H.264/AAC stereo, 17.4 MB with fast start. Export, decode, audio and visual checks passed; captions, transcript, contact sheet and source-hash manifest are saved locally. No narration pickup is required. Private contact/account details and unrelated camera-gallery thumbnails are masked. Continuous Mac recording failed; no continuous capture is claimed. Preservation of the original 21-minute Record it! source is unconfirmed; the received exports and earlier illustrated film remain intact. The revised video is [published unlisted](https://youtu.be/R1EKxswOsns), HD processing is complete, and its URL is saved in the Devpost draft. |
| Judge access | `aws-cds-partner` accepted the private-repository invitation and has write access. Retain access through 2026-11-19. Judges inspect the default branch. |
| Submission | Devpost remains a draft. The revised unlisted video URL is saved, with four of five steps complete; there is no final terms acceptance or submission receipt. Architecture, repository/judge access details and the completed live result are saved. Required ACE campaign association and the user's final submission authorization remain pending; the latest authorization was to upload the video only. |

The requested deadline is **2026-10-28 13:00 PDT / 20:00 UTC / 21:00 Stockholm**. Carrier
review and SMS production access remain external dependencies. The accepted two-phone demo uses the one-way sender ID
for SMS alerts; it does not establish dedicated-number provisioning or real RCS delivery.

Raw receipts, contact details and logs remain in the ignored `demo/deployment-state/` directory.
Do not publish it. The live sequence and recording script are in [the runbook](../demo/RUNBOOK.md).
