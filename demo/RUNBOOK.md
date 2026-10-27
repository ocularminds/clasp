# Clasp deployment and three-minute demo

Run commands from the repository root. Configuration and personal test contacts belong in the
ignored `.env`; keep raw logs, codes, recordings and deployment responses in the ignored
`demo/deployment-state/` and `demo/recordings/` directories. See
[deployment status](../docs/deployment-status.md) for what has actually been verified.

## 1. Account and prerequisites

Use Python 3.12, `uv`, and the Node version in `.node-version`. The CDK CLI is pinned by
`package-lock.json`; `make deps` installs it with `npm ci`.

```bash
aws login --profile default --region us-east-1
aws sts get-caller-identity --profile default
# On a new checkout only; preserve an existing configured .env.
cp .env.example .env
chmod 600 .env
make deps
make test
```

Set `CDK_DEFAULT_ACCOUNT` to the intended account, and keep both region settings at `us-east-1`.
Buyer and seller need different E.164 phone numbers. `DEMO_BUYER2_PHONE` is optional;
`DEMO_SUPERVISOR_PHONE` may be empty because approvals arrive by email. A live seed rejects
placeholder phones and phone collisions. Using one verified mailbox for multiple demo email
roles is supported, although separate mailboxes make the video clearer.

### WhatsApp

Open [End User Messaging Social](https://us-east-1.console.aws.amazon.com/social-messaging/home?region=us-east-1).
Complete Meta embedded signup and link the intended business account. Record the AWS
`phone-number-id-...` identifier in `WHATSAPP_PHONE_NUMBER_ID` and the AWS linked WABA identifier
in `WHATSAPP_WABA_ID`; do not substitute a display phone number for either identifier.

If Meta says the number already belongs to a WhatsApp account, determine whether it is in
the regular phone app, the Business app, or another API provider. AWS's
[signup prerequisites](https://docs.aws.amazon.com/social-messaging/latest/userguide/getting-started-whatsapp.html)
require removing a number from the phone app before reusing it. The phone owner should export
needed chats and deliberately complete that migration, then continue embedded signup and its
SMS/voice verification. Do not delete the existing account as a routine retry. A dedicated
unused business number is another option.

### SMS and RCS

1. Register each test phone as a verified destination and complete its SMS code verification.
2. Create an AWS RCS agent and associate a `TEST_RCS_LAUNCH_REGISTRATION`. Supply the brand,
   public privacy and terms URLs, and `docs/assets/rcs-logo.png` / `rcs-banner.png`.
   Save `RCS_AGENT_ID` and `RCS_REGISTRATION_ID` in `.env`.
3. Wait until the test registration and RCS agent are active. Create an RCS-specific verified
   destination for each test device using `--rcs-agent-id`. This is separate from SMS sandbox
   verification. The phone owner must accept the RCS tester invitation on the device.
4. Request an SMS origination number in the recipients' country and create a pool with the SMS
   originator. Save `RCS_POOL_ID` and `SMS_ORIGINATION_IDENTITY`. Configure the RCS agent's
   routing and media settings in section 2 before associating its ARN with that pool.
5. A Swedish `CLASP` sender ID is available for one-way demo SMS while the Swedish long-code
   registration is pending. It cannot receive SMS replies and is not a dedicated phone number.
   Do not buy a Canadian number as a substitute for a Swedish originator: AWS numbers are
   country-specific. The [Swedish registration](https://docs.aws.amazon.com/sms-voice/latest/userguide/registration-eta.html)
   has an estimated approval time; it is not an immediate console allocation.

Both the agent and pool must have matching HELP/STOP keywords, opt-out settings and two-way
SNS destination before association. An otherwise valid association can fail with
`KEYWORD_MISMATCH`. Preserve STOP's `OPT_OUT` action while aligning the keyword responses.

The RCS destination must show `VERIFIED` for that agent. SMS sandbox verification alone is
insufficient. `UNSUPPORTED` indicates that the RCS test-device setup has not succeeded; do not
count an SMS fallback as an RCS delivery. On iPhone, both carrier RCS support and RCS Business
Messages matter. On Android, Google Messages should report RCS chats as connected before a
new tester invitation is requested.

For a testing agent, AWS can reject an unverified RCS destination before applying the request's
SMS fallback. The router handles that specific rejection by calling `SendTextMessage`; SMS
still enforces its own destination verification and opt-out rules. The log records
`requested_channel=rcs` and `channel=sms`. This enables SMS alerts, not RCS replies or photos.
Other RCS rejections, including an opted-out destination, are not retried through SMS.

The test registration needs a real privacy notice that covers this AWS demo. A policy describing
only a different cloud service should be reviewed before it is attached to the RCS profile.

### SES and Bedrock

Verify `SES_FROM_EMAIL`, `SUPERVISOR_EMAIL`, and every demo recipient address in SES in
`us-east-1`. While SES is in its sandbox, both sender and recipient identities must be verified.
If all roles use one mailbox, one successful identity verification covers that address.

For the Anthropic model in `BEDROCK_MODEL_ID`, complete the Bedrock first-use form and confirm
access with a small Converse request. The default is the US inference profile
`us.anthropic.claude-sonnet-4-5-20250929-v1:0`; use the same model for evidence in this demo.

### SES production access and the SMS sandbox

The live `us-east-1` account check on 27 October at 08:25 UTC confirmed SES production access and healthy
sending. SMS still reported `SANDBOX` at 08:20 UTC, with both the enforced and maximum monthly text-message
limits at $1. The owner-reported SMS approval and $25 limit have not yet appeared in the service configuration.
Until the account tier changes, SMS recipients must be verified destinations. SES no longer requires recipient
mailboxes to be verified.

The SMS origination identity is the Swedish `CLASP` sender ID. Alphanumeric sender IDs are country-specific, and
the US and Canada don't accept them, so a text to a non-Swedish judge's number is expected to fail until an
origination identity for that country is in the pool. When an SMS send is refused, the buyer's delivery code
goes to their WhatsApp chat instead, and seller notices go by email.

The SES production-access request and status check are retained here for reference:

```bash
aws sesv2 put-account-details --production-access-enabled --mail-type TRANSACTIONAL \
  --website-url "https://binariesfort.com" --contact-language EN \
  --additional-contact-email-addresses "$SES_FROM_EMAIL" \
  --use-case-description "Clasp, a conversational-commerce demo for the AWS CDS Agentic AI Hackathon, sends order receipts, dispute evidence packs and supervisor approval links to the buyer, seller and supervisor of each order. Every recipient is a participant in an order they initiated; volume under 200 messages a day during judging; no marketing mail."
aws sesv2 get-account --query '{production:ProductionAccessEnabled,status:EnforcementStatus}'
```

Check the SMS tier and limits before relying on production access:

```bash
aws pinpoint-sms-voice-v2 describe-account-attributes --region us-east-1
aws pinpoint-sms-voice-v2 describe-spend-limits --region us-east-1
```

The SMS production-access and $25/month requests are already filed in Support case `179305047100524`.
The request path is End User Messaging SMS console → **Overview** → the sandbox
notice → **Request production access** (opens a Support Center case pre-filled for SMS). State the use case
(one-time delivery codes and order alerts to order participants, Sweden, transactional, opt-in by placing an
order), the expected volume (under 100 a day) and the sender (`CLASP` sender ID, long code pending). Until it is
granted, verify each judge number instead:

```bash
aws pinpoint-sms-voice-v2 create-verified-destination-number --destination-phone-number "+46XXXXXXXXX"
aws pinpoint-sms-voice-v2 send-destination-number-verification-code --verified-destination-number-id <id> --verification-channel TEXT
aws pinpoint-sms-voice-v2 verify-destination-number --verified-destination-number-id <id> --verification-code <code from the handset>
```

Judges reach the agent on WhatsApp once `AUTO_ENROL_CHANNELS=whatsapp` is deployed. The SMS sandbox still gates
delivery codes sent to unverified numbers; SES production access permits email receipts to unverified mailboxes.

## 2. Deploy, bind topics, and seed

```bash
make bootstrap
make diff
make deploy
```

`make deploy` writes CDK outputs to `.env` and selects the live store, workflow, notification
and model modes. SES identities are managed outside CDK because they were verified before
deployment. Load the resulting values into the shell:

```bash
set -a
source .env
set +a
```

The two topic bindings are:

```bash
aws socialmessaging put-whatsapp-business-account-event-destinations \
  --region "$AWS_REGION" --profile "$AWS_PROFILE" \
  --id "$WHATSAPP_WABA_ID" \
  --event-destinations "eventDestinationArn=$INBOUND_WHATSAPP_TOPIC_ARN"

aws pinpoint-sms-voice-v2 update-pool \
  --region "$AWS_REGION" --profile "$AWS_PROFILE" \
  --pool-id "$RCS_POOL_ID" --two-way-enabled \
  --two-way-channel-arn "$INBOUND_SMS_TOPIC_ARN"
```

Also enable two-way messaging on the **RCS agent itself**, including photo storage, before
associating it with the pool. The pool binding alone does not configure inbound media delivery.
AWS rejects `UpdateRcsAgent` while the agent belongs to a pool. For an existing association,
temporarily disassociate only the RCS agent, wait until `DescribeRcsAgents` no longer returns its
`PoolId`, apply the update, and restore the association. Keep the SMS originator in the pool.

```bash
aws pinpoint-sms-voice-v2 update-rcs-agent \
  --region "$AWS_REGION" --profile "$AWS_PROFILE" \
  --rcs-agent-id "$RCS_AGENT_ID" --two-way-enabled \
  --two-way-channel-arn "$INBOUND_SMS_TOPIC_ARN" \
  --two-way-media-s3-bucket-name "$MEDIA_BUCKET" \
  --two-way-media-s3-key-prefix inbound \
  --two-way-media-s3-role "$RCS_MEDIA_ROLE_ARN"

# After matching the agent and pool's keywords and opt-out settings:
RCS_AGENT_ARN=$(aws pinpoint-sms-voice-v2 describe-rcs-agents \
  --region "$AWS_REGION" --profile "$AWS_PROFILE" \
  --rcs-agent-ids "$RCS_AGENT_ID" --query 'RcsAgents[0].RcsAgentArn' --output text)
aws pinpoint-sms-voice-v2 associate-origination-identity \
  --region "$AWS_REGION" --profile "$AWS_PROFILE" \
  --pool-id "$RCS_POOL_ID" --origination-identity "$RCS_AGENT_ARN" --iso-country-code ZZ

uv pip install --python .venv/bin/python bedrock-agentcore-starter-toolkit
make agent-deploy
make seed
make kb-sync                     # policy documents into the Knowledge Base; waits for the ingestion job
```

The deployment wrapper packages the complete `services/` tree, reads the runtime ARN from
the toolkit state, publishes it to SSM, and applies KMS encryption and 14-day retention to
runtime logs. The starter toolkit currently displays a deprecation notice; this wrapper uses
its tested container deployment path. The CDK-managed Gateway and Memory are reused.

The ingress role needs both `bedrock-agentcore:InvokeAgentRuntime` and
`bedrock-agentcore:InvokeAgentRuntimeForUser` because it passes the resolved actor identity.
The RCS upload role also permits EUM's setup notification at
`inbound/AWS_END_USER_MESSAGING_SETUP_NOTIFICATION`; actual media stays under `inbound/rcs/`.

If a phone ID, pool, sender ID or other channel value changes after deployment, run `make diff`
and `make deploy` again so the Lambda environment picks it up. Never publish `.env` or toolkit
state. Confirm the runtime is `READY` before starting a phone conversation.

## 3. Real phone run (autonomous flow)

The order runs itself: the buyer talks to Clasp on WhatsApp, the seller only receives one-way notices
(RCS, falling back to SMS, then email) and is asked to decide only when policy escalates. A courier pickup
scan, not a seller message, records dispatch. RCS stays configured but **untested on a supported handset**.

Before recording:

- **Deploy master** (`make deploy`, then `make agent-deploy` for the prompt and tools, then `make kb-sync` for the
  policy document). The autonomous flow was first deployed on 27 October. Set `COURIER_MODE=manual` in `.env` for the recording so the courier step is
  on camera; set it back to `auto` for judges.
- **Decionis AgentSafe gateway (enforcement).** With `AGENTSAFE_URL` set, an enforcing gateway is the only way to a
  decision: if it can't answer, the request is denied as `decionis_unavailable` and nothing moves. The checkout page
  then tells the buyer the payment didn't go through and they can press Pay again (the mock PSP doesn't redeliver);
  the seller's settlement retries in Step Functions. Clasp's tenant is `clasp` at `https://clasp.decionisedge.com`
  (its `/_agentsafe/readyz` reports `ENFORCEMENT`; the older `dci` host is still in shadow). Set it up in this
  order:
  1. Store the `clasp` tenant key with
     `aws secretsmanager put-secret-value --secret-id clasp/dev/decionis-tenant-key --secret-string "$KEY"`.
  2. Set the tenant's `upstream` to the origin of `$API_BASE_URL`. The gateway relays the same path, so calls to
     `https://clasp.decionisedge.com/decide` reach `$API_BASE_URL/decide`.
  3. Set its `upstreamCredential` to header `authorization` with the file contents `Bearer <value of
     clasp/dev/agentsafe-upstream-token>`. An enforcing gateway drops the agent's own value and sets this one.
  4. Select the published policy **`clasp`**. The hosted gateway's managed workspace credential is bound to this
     policy name, so Decionis selects its newest active version. At the verified 27 October checkpoint, that is
     `clasp-gateway-2026.10.27-1`, bundle `e6078687-55e5-4323-98bc-7f2edc9b9b88`. The two earlier requests had no matching
     policy and returned `202 ESCALATE` with `PROTOCOL_OUTCOME_REVIEW,POLICY_MISMATCH`; the checkout stayed unpaid.

     For a new policy version, validate its native bundle with `POST /v1/policies/validate`, then publish through
     `POST /v1/protocol/policies/bundles` using authorized operator access. Supply `policy_id: "clasp"`, the intended
     organization, a unique bundle UUID and version, an effective date, and the rule below. Read it back through
     `GET /v1/protocol/policies/bundles` for that organization and confirm ACTIVE with the expected payload.
     A saved authoring draft or an HTTP 202 publication acknowledgement alone does not establish active selection.

     In the managed deployment settings, associate policy `clasp` while retaining the existing upstream credential
     and ENFORCEMENT mode. Confirm the gateway workspace credential reports `policy_id: "clasp"`. This tenant's
     registry is maintained by the provisioner; manual route edits can be overwritten. The current deployment has
     no explicit `/decide` route and names the request **`http.post`**. Its published rule is:

     ```json
     {
       "rule_id": "clasp-decide-allow",
       "name": "Clasp money decisions are decided by Clasp's policy service",
       "decision_domain": "*",
       "priority": 100,
       "enabled": true,
       "when": {"all": [
         {"field": "transaction_type", "operator": "EQ", "value": "http.post"},
         {"field": "context.agent_safe.action.parameters.path", "operator": "EQ", "value": "/decide"},
         {"field": "context.agent_safe.action.parameters.body.action", "operator": "IN",
          "values": ["discount", "hold", "release", "refund", "credit", "replacement", "settle"]}
       ]},
       "then": {"action": "AUTO_APPROVE"},
       "rationale": "Clasp's reference policy enforces caps, evidence, velocity and seller/supervisor approval, and signs the authorization. This rule admits only POST /decide with a known Clasp money action to that policy service; it does not itself execute a money action."
     }
     ```

     The gateway (AgentSafe 0.7.0) has its workspace credential bound to the policy name `clasp` and evaluates the
     newest `ACTIVE` version of that policy. This is not an immutable pin. Publishing a new version changes the live
     rule without a Clasp deploy, so run the step 6 check again after any publish.

     Why it has this shape:
     - The gateway names a call by its route. With no route for `/decide` the name is `http.post`, and that name
       is the `transaction_type` the rule matches. The path condition keeps the rule to `/decide`. If you add a
       route that renames the action (for example `clasp.decide`), change and retest the rule at the same time.
     - Selection is owned by gateway configuration and its bound workspace credential. Putting `policy_id` inside
       Clasp's HTTP body does not select a bundle. Runtime 0.7.0 also supports operator route selectors, but they
       are not configured on this managed tenant.
     - The intent's parameters are the HTTP call (`method`, `path`, `query`, `body`), so Decionis doesn't read
       Clasp's `amount_minor` as an amount. Body fields are still reachable as rule facts under
       `context.agent_safe.action.parameters.body`. The rule has no amount cap; Clasp's reference policy enforces
       caps, evidence, velocity and approvals.
     - Only `AUTO_APPROVE` yields ALLOW. With no matching rule the outcome is review, which the gateway answers
       as `202 ESCALATE`.
     - Don't use `ESCALATE` as the rule's action: that is Decionis-managed review, and a seller's approval sent
       back through the gateway would be held again.
     - Check the action name the gateway actually used in the held call's dossier. `make decisions` prints
       `dossier=` with the first 8 characters of the dossier id that Clasp records with each gateway decision.
     - The `{"all": [...]}` grouping and `values` for `IN` passed 19 native-core cases, API validation, and the
       live hold. Dossier `f7f1f55e…` records `clasp-decide-allow` as the selected rule.
  5. Put the origin proof from `make upstream-proof TENANT=clasp ORG=<organization uuid>` in `.env` as
     `AGENTSAFE_UPSTREAM_PROOF`, set `AGENTSAFE_URL=https://clasp.decionisedge.com` and
     `AGENTSAFE_GATEWAY_MODE=enforcement`, and run `make deploy`. Clasp serves the proof at
     `/.well-known/agentsafe-upstream`.
  6. Run `make gateway-check`, which reads the gateway's mode and relays one unevaluated GET with the tenant key.
     `make gateway-check` doesn't send a decision, so it proves neither the upstream credential nor the rule.
     The [27 October synthetic $1 hold](../docs/gateway-verification-2026-10-27.md) already completed; preserve it
     and do not press Pay again on that fixture. For a new smoke test, verify active publication and policy binding
     first, then send Pay once on a fresh synthetic order. Require HTTP 200, order `HELD`, exactly one HOLD ledger
     entry, and `allow    decionis  hold` with a `dossier=` id in `make decisions`. Retrieve that dossier and check
     that its policy snapshot and execution binding identify the expected bundle and version. A gateway readiness
     response or engine label alone cannot prove which policy was selected. `decionis` means the call came through
     the gateway; `reference` would mean it was decided directly, which enforcement refuses.
     On 27 October one Pay on that synthetic $1 checkout gave
     `17:40:47 allow    decionis  hold  $1.00 … in_policy  dossier=f7f1f55e`, with one `HOLD` in the ledger. Clasp's
     adapter logged the gateway's `execution=FORWARDED`, which proves the upstream credential.
     If the checkout says the payment didn't go through:
     - a `decionis_escalate: …POLICY_MISMATCH` decision means the selected policy did not match. Check the policy
       binding, publication state and action/path rule (step 4). Confirm the order remains unpaid with no ledger
       entry before one retry after the correction; repeated Pay requests otherwise add more reviews;
     - an `agentsafe.gateway_unavailable` log with `upstream HTTP 401` means the gateway's `upstreamCredential`
       doesn't match `clasp/dev/agentsafe-upstream-token` (step 3).

  The tenant's Decionis policy should allow or block Clasp's `POST /decide` calls and leave human review to Clasp.
  A held Truce request (refund, credit, replacement) becomes a Clasp escalation to the seller, but the same call
  made again after the seller approves would be held again. A held hold, release or settle has no reviewer on
  Clasp's side: the payment, payout or settlement simply doesn't happen. The request body carries `action`,
  `amount_minor`, `currency`, `evidence` and, on an approval, `context.approval_stage`, so a rule can tell these
  calls apart.
- **Use a buyer identity that hasn't had a refund in 30 days.** Both seeded demo identities used their one refund on
  26 October, so a refund for them is denied for velocity. Message the business number from a third WhatsApp number:
  `AUTO_ENROL_CHANNELS=whatsapp` enrols it as a buyer on first contact. Give the assistant an email address in the
  chat so the receipt and evidence pack reach a mailbox you can show.
- **SMS.** The latest service check still shows `SANDBOX` and a $1 monthly limit. Use verified destinations until
  production access is reflected in the account, and check that the requested $25 ceiling has taken effect. If a
  send is refused, the code arrives in WhatsApp and seller notices by email. Narrate whichever channel actually
  carried each message.
- Prepare the item (the camera bag or lens; the Zoom H6 is out of stock), paper for the challenge code, and a phone
  to act as the courier.

Start the ingress and decision logs in separate terminals:

```bash
aws logs tail /aws/lambda/clasp-dev-ingress --region us-east-1 --since 10m --follow
make decisions
```

1. From the **buyer handset**, message the WhatsApp business number: "Is the camera bag available? Can I get 10%
   off?" The reply comes from Strands on the live Bedrock model. Give the email address when the agent offers.
2. Open the hosted checkout link and press Pay. It is a **simulated payment**. The page shows "Payment confirmed";
   the buyer gets an email receipt and the seller's phone shows the one-way "Payment secured" notice.
3. **Courier pickup.** Run `make courier ORDER=<6-character code>` and open the printed link on the courier phone.
   Photograph the parcel and press "Scan pickup". The buyer's WhatsApp shows the pickup photo and the delivery code
   arrives by SMS (or WhatsApp; see above). The seller does nothing.
4. **Do not redeem the code** for the dispute scenario: redeeming it releases the payout. Tell the agent the item
   arrived damaged. Clasp calls a Truce and returns a challenge code. Write it on paper next to the item and send one
   photo or a video under 30 seconds within 15 minutes.
5. Ask for a $1,000 refund. Show AgentSafe's denial, worded for the buyer in the chat ("That's more than the $108.00
   held for this order.") and the unchanged ledger in `make decisions`. The deployed prompt always submits the
   amount asked for.
6. Tap **20% partial refund**. One of two things happens, and both are worth filming:
   - the photo clearly shows damage: AgentSafe allows it, the buyer gets the refund in the chat, and the seller's
     notice says the remaining $86.40 has been released to them;
   - the photo doesn't clearly show damage (as in the 26 October take): this is **policy drift**. AgentSafe escalates,
     the buyer is told the seller has been asked, and the seller's email asks them to approve. Open the link (a
     confirmation page; nothing changes yet), press Approve, and show the buyer's WhatsApp: "the seller approved".
   To film the above-cap path instead, ask for the full $108: it escalates to the seller; rejecting it hands the
   decision to the supervisor email.
7. Show the evidence pack email (outcome, who decided, the evidence check and the ledger) and the console: the
   Step Functions graph, the decision log and the ledger (`HOLD`, `REFUND`, `RELEASE`).

Do not use `make simulate --live` as evidence of a handset run. The automated simulator calls tools directly and uses
placeholder photos. `make simulate ARGS="--chat --live --agent llm"` is a diagnostic console conversation, not a phone
recording.

### Optional first real RCS event

This remains a separate, unfinished channel check. Run it only with a supported device whose
RCS destination is `VERIFIED` for the agent; the available iPhones currently lack RCS settings.
Do not label an SMS fallback or a synthetic SNS event as a handset RCS test.

On a future supported seller device, inspect the RCS "Payment secured" card, then send the agent a message or a
photo. Check `ingress.record`, followed by `agent.turn.in` with `channel=rcs`.
Text events use the RCS agent ID as `destinationNumber`; taps have JSON `messageBody` with
`postbackData`; photos have `mediaStatus=SUCCESS`, `s3Bucket`, and `s3Key`. The photo should
arrive under `inbound/rcs/` in the configured bucket. Ingress logs field/type shape without
logging raw media URLs; use that shape to diagnose the first actual event before proceeding.

### Getting the fourth channel (RCS) onto a real handset

RCS cannot be fixed in code: the two available iPhones (Tre, Lyca) expose no RCS setting, so both test
destinations stay `UNSUPPORTED` and the router's SMS fallback is what ships. To record the RCS beat for real:

1. Use an **Android phone with Google Messages as the default SMS app** on a carrier where
   *Settings → RCS chats* shows **Connected** (that is the carrier having launched RCS Business Messaging;
   verify on the handset rather than trusting a carrier list).
2. Register it as a test destination for the RCS agent and accept the tester invitation that Google Messages
   shows: `aws pinpoint-sms-voice-v2 create-verified-destination-number --destination-phone-number "+46…" --rcs-agent-id "$RCS_AGENT_ID"`,
   then the verification call above. The destination must read `VERIFIED` **for that agent**; SMS sandbox
   verification is a different list.
3. Put that number in `.env` as `DEMO_SELLER_PHONE`, run `make seed`, and send one order through. The "Payment
   secured" rich card should render on the handset. It is one-way: the seller needs no reply. To see an inbound RCS
   turn, have the seller message the agent ("my orders?") and watch `ingress.record` then `agent.turn.in` with
   `channel=rcs` in the ingress logs.
4. Only then change the narration line about RCS. Until a `channel=rcs` turn has been observed, the honest
   wording stays: "RCS is configured and falls back to SMS; handset delivery is untested."

## 4. Three-minute recording script

Use [the conversational narration](NARRATION.md) for the voice recording. The table below is
the shot guide; do not read its technical labels aloud.

Capture the successful workflow before editing. Preserve its order and label any accelerated
waiting time. Open with three seconds of the typewritten ASCII Clasp logo, then show the real
buyer, seller and email/console views in a split screen. Keep the cropped narrator small at
bottom-left without covering the phone text. Show the final console evidence before the last
five seconds of Clasp branding. Never display verification codes, credentials, account recovery details,
or unrelated inbox contents. The demo delivery/challenge codes may be visible after the run
has completed and they can no longer authorize an action.

| Time | Picture | Narration |
|---|---|---|
| 0:00–0:03 | Typewritten ASCII Clasp logo | Opening narration begins. |
| 0:03–0:20 | Buyer phone, seller phone, email/console panel | "Buying in a chat is easy. Trusting what happens next is the hard part. Clasp runs the order itself, and every action that moves money needs a signed policy decision." |
| 0:20–0:45 | Buyer asks, receives 10% offer, simulated checkout | "Claude on Amazon Bedrock checks live stock through AgentCore and sends a checkout link. Payments are simulated; only the provider's signed confirmation puts the money on hold." |
| 0:45–1:05 | Seller's one-way "Payment secured" notice; courier page photographs the parcel; buyer sees the pickup photo | "The seller doesn't chat. They're told the funds are secured, and a courier's signed pickup scan records dispatch. The buyer gets the pickup photo and a one-time delivery code." |
| 1:05–1:45 | Damage report, challenge code, live photo, "$1,000" request and the denial in the chat | "When something goes wrong, Clasp calls a Truce. A fresh code next to the item proves the photo is new. Asking for a thousand dollars can fool the model into proposing it; AgentSafe denies it, with a reason." |
| 1:45–2:25 | Buyer taps 20%; either automatic settlement, or drift → seller approval page → buyer told | Automatic: "Within policy, the Truce settles in the chat, and the rest of the payment goes to the seller." Drift: "The photo doesn't clearly show damage, so even an in-policy refund goes to the seller. They approve on a confirmation page; the buyer hears back in the chat." |
| 2:25–2:55 | Evidence pack email; console: Step Functions, decisions, ledger | "Both sides get the evidence pack. Every decision, allowed, denied or escalated, passed through Decionis AgentSafe, and only the executor can write the ledger." |
| Last 5 seconds | Clasp closing animation | "Clasp: nothing moves until both sides are kept whole." |

If the recorded evidence differs from the script, update the narration. Show the actual decision
and status; a saved script is not proof that the live run succeeded.

## 5. Exports and submission

```bash
uv pip install --python .venv/bin/python -e '.[assets]'
.venv/bin/python demo/export_assets.py
```

`docs/architecture.png` is the Devpost architecture export. Image dimensions, hashes and brand
source are recorded in `docs/assets/manifest.json`. The architecture describes the implemented
CDK resources; it does not claim a Knowledge Base or EventBridge integration.

Save the real phone captures and final console shot under the ignored `demo/recordings/`.
The accepted revised edit uses the supplied cleaned narration, four Zoom H6 replacement lines,
a three-second logo opening, real workflow footage, a small square narrator at bottom-left,
readable captions and a five-second Clasp closing. Adjust the body to the finished voice track;
keep the complete export under three minutes. The live footage and narration replacements
must be captured before that edit is rendered. Preserve the earlier illustrated presentation.

The command below is the **earlier split-screen composer**, retained for that simpler layout.
It does not add the logo bookends, narrator inset or captions. Prepare synchronized,
edited clips covering the first 160 seconds: two portrait phone captures and one email/console
capture. The script preserves the full frame of each source. Crop the desktop recording to the
relevant message or console view before assembly so text remains legible. Narration should span
180 seconds, including deliberate pauses; do not generate dialogue as if it were recorded app output.

```bash
.venv/bin/python demo/compose_demo.py \
  --buyer demo/recordings/buyer.mp4 \
  --seller demo/recordings/seller.mp4 \
  --panel demo/recordings/email-console.mp4 \
  --console demo/recordings/final-console.mp4 \
  --narration demo/recordings/narration.m4a
```

The composer allocates 480 pixels to each phone and 960 pixels to the email/console panel, then
shows the final 20-second console clip full screen. It exports a 1920 × 1080, 180-second MP4,
requires actual recordings, and refuses to overwrite an existing output. `--third` remains an
alias for `--panel` for older commands. Review the full export and check phone text at playback size.

Before submitting the existing [Devpost project](https://devpost.com/software/clasp-nothing-moves-until-both-sides-are-kept-whole),
upload the architecture, add a working public or unlisted YouTube/Vimeo demo-video URL, check the
project text against the recorded evidence, and review the final rules/terms checkbox. Confirm
that the repository link is accessible to the judges. The [hackathon instructions](https://github.com/aws-cds-partner/aws-cds-agentic-ai-partner-hackathon-private/blob/main/README.md)
allow a private repository with the `aws-cds-partner` judging account invited. The owner approved
that personal-repository write access, and the invitation was sent on 2026-10-25. Verify acceptance
before judging; the invitation link is `https://github.com/ocularminds/clasp/invitations`.
The judges inspect the default branch's latest commit. Keep access through 2026-11-19.

The draft already contains an ACE opportunity ID. Verify its creation date and the
`AWS CDS Agentic AI Hackathon -Sept. 2026` campaign association rather than creating a duplicate.
An opportunity pending AWS review may have disabled edit controls; add the campaign when AWS
allows edits before treating that submission requirement as complete. The user confirmed no AWS
Marketing Development Funds funding, so use `AwsFundingUsed=No`. Promotional credits are separate.
`UpdateOpportunity` replaces the writable document: fetch the latest opportunity, preserve all
existing writable fields, and use its current `LastModifiedDate` when adding the marketing fields.

The deadline is **Wednesday 2026-10-28, 13:00 PDT**: **20:00 UTC / 21:00 Stockholm**. A saved draft
is not a submitted project. Confirm the submission success page and save its receipt.
