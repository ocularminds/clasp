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

## 3. First real phone run

Prepare a parcel/camera bag, a seller dispatch photo, a visibly damaged item/strap, and paper
for the fresh challenge code. Use real photos; the simulator's placeholder JPEG bytes cannot
exercise Bedrock vision.

Open three recording views: buyer WhatsApp, seller RCS, and SMS/email. For a literal three-phone
shot, mirror or record three actual devices. The third phone can show the supervisor mailbox;
the SMS delivery code still goes to the configured buyer phone. Start one visible timer or clap
to synchronize the recordings. Disable unrelated notification previews before recording.

Start the ingress and decision logs in separate terminals:

```bash
aws logs tail /aws/lambda/clasp-dev-ingress --region us-east-1 --since 10m --follow
make decisions
```

1. From the **buyer handset**, message the WhatsApp business number: “Is the vintage leather
   camera bag available? Can I get 10% off?” The first reply should come from Strands using the
   live Bedrock model. Keep the conversation inside WhatsApp's customer-service window.
2. Accept the order and open the hosted checkout link. The button creates a **simulated payment**;
   no card or bank credentials are needed. Confirm the order reaches `HELD` before dispatch.
3. On the seller phone, inspect the RCS card and tap “Upload dispatch photo.” Send the real photo
   and identify the order if asked. Watch the **first actual RCS event** in ingress logs.
4. Confirm an `ingress.record` entry, followed by `agent.turn.in` with `channel=rcs`. Text messages
   have an RCS agent ID as `destinationNumber`; taps have JSON `messageBody` with `postbackData`;
   photos have `mediaStatus=SUCCESS`, `s3Bucket`, and `s3Key`. The log records payload field/type
   shape without logging those media URLs or extra raw values. Use that shape to diagnose any
   mismatch before continuing.
5. The dispatch photo must appear under `inbound/rcs/` in the media bucket, and the order should
   become `DISPATCHED`. Confirm the SMS delivery code arrives on the buyer phone.
6. For the Truce path, **do not redeem the delivery code**. Say “The strap arrived torn.” The
   workflow holds the payout and returns a fresh challenge. Write that exact code next to the
   item, photograph it, and send the image through WhatsApp before the challenge expires.
7. Request a $1,000 refund. Show an actual AgentSafe denial and an unchanged ledger. If the model
   declines before calling a tool, explain that accurately; do not label it a policy-service
   decision. The deterministic simulator separately demonstrates a forced excessive proposal.
8. Choose the partial refund offered by policy. Show the resulting signed decision, ledger entry,
   and evidence email. For a happy-path order instead, redeem the SMS code and confirm `RELEASED`.

Do not use `make simulate --live` as evidence of a handset run. The automated simulator calls
tools directly and uses placeholder photos; its immediate steps also do not wait for real
Step Functions callbacks. `make simulate ARGS="--chat --live --agent llm"` is a diagnostic
console conversation, not a phone recording.

### Above-cap approval scenario

The policy permits one refund per buyer per 30 days. To film both a completed partial refund
and a completed supervisor-approved refund, use a **second verified buyer identity** and a
second order. Do not erase a real decision or ledger entry to bypass this rule.

On the second order: pay in the mock checkout, dispatch, report damage, verify the fresh photo,
and request the full held amount. Confirm `ESCALATED`, open the SES supervisor email, and
follow its signed Approve link. Show the resulting refund and evidence pack. Each approval is
bound to that proposal and the links expire after 48 hours.

With only one buyer available, demonstrate escalation first or choose one resolution path;
do not claim that both completed on the same buyer after a previous refund.

## 4. Three-minute recording script

Capture the successful workflows before editing. Preserve their order and label any accelerated
waiting time. Keep the buyer, seller, and SMS/email views visible in one split screen until the
final console shot. Never display verification codes, credentials, account recovery details,
or unrelated inbox contents. The demo delivery/challenge codes may be visible after the run
has completed and they can no longer authorize an action.

| Time | Picture | Narration |
|---|---|---|
| 0:00–0:20 | Three real phone views; Clasp title | “Chat marketplaces have a trust gap. Clasp coordinates the purchase, but every action affecting money passes a separate policy check.” |
| 0:20–0:50 | Buyer asks, receives 10% offer, uses mock checkout | “Claude checks live inventory through AgentCore Gateway. The checkout is a payment simulation; a signed provider webhook confirms the hold.” |
| 0:50–1:10 | Seller RCS card, suggestion tap, dispatch photo | “The seller receives an RCS card and sends the parcel photo. The workflow records the dispatch.” |
| 1:10–1:25 | Buyer SMS delivery code; then damage report | “An SMS code confirms delivery. Here the buyer reports damage before releasing the payout, so Clasp calls a Truce.” |
| 1:25–2:15 | Fresh photo challenge, actual denial, offered partial refund | “The image is evidence, not permission. AgentSafe checks the requested amount, identity and policy. The $1,000 request is refused; a permitted resolution gets a signed authorization.” |
| 2:15–2:40 | Second buyer/order; SES approval and evidence | “A larger refund needs a supervisor. This separate order waits for the signed approval link before anything executes.” |
| 2:40–3:00 | Console full screen: Step Functions, decisions, ledger | “Only the executor can append the ledger. It verifies the signature, exact scope, expiry and one-use nonce. Nothing moves until both sides are kept whole.” |

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

Save the real phone captures and final console shot under `demo/recordings/`. The optional
`demo/compose_demo.py` script assembles prepared 160-second phone clips and a 20-second console
clip into a 1080p three-minute video; it requires actual input recordings.

Before submitting the existing [Devpost project](https://devpost.com/software/clasp-nothing-moves-until-both-sides-are-kept-whole),
upload the architecture, add a working public or unlisted YouTube/Vimeo demo-video URL, check the
project text against the recorded evidence, and review the final rules/terms checkbox. Confirm
that the repository link is accessible to the judges. The [hackathon instructions](https://github.com/aws-cds-partner/aws-cds-agentic-ai-partner-hackathon-private/blob/main/README.md)
allow a private repository with the `aws-cds-partner` judging account invited. Personal GitHub
repositories give collaborators write access; obtain the owner's approval before granting it.
The judges inspect the default branch's latest commit. Keep access through 2026-11-19.

The draft already contains an ACE opportunity ID. Verify its creation date and the
`AWS CDS Agentic AI Hackathon -Sept. 2026` campaign association rather than creating a duplicate.
An opportunity pending AWS review may have disabled edit controls; add the campaign when AWS
allows edits before treating that submission requirement as complete.

The deadline is **Wednesday 2026-10-28, 13:00 PDT**: **20:00 UTC / 21:00 Stockholm**. A saved draft
is not a submitted project. Confirm the submission success page and save its receipt.
