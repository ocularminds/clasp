# Clasp — final live-demo narration

This is the selected original voice used in the 1:53.933 live demo for order `27e389`.
The owner chose the completed 20% partial-refund take. The edit uses cuts of the supplied
narration and matching face video; no new speech or narration pickups are required.
The earlier original recording and illustrated film are preserved.

The three-second typewritten Clasp opening is silent. Real phone recordings and labelled
post-completion email/console captures fill the body. The narrator remains in a small
square at bottom-left. The closing runs for five seconds.

## Selected narration

**0:03** Buying something through chat is easy.

**0:06** Trusting what happens next is the hard part.

**0:09** Clasp helps a buyer and seller complete their purchase and work things out if something goes wrong.

**0:16** Today we are using WhatsApp, SMS, and email.

**0:20** Claude on Amazon Bedrock checks the live catalog

**0:23** through AgentCore and creates a checkout link.

**0:27** For this prototype, payments are simulated.

**0:31** The order is only marked as held

**0:34** after the payment service sends confirmation.

**0:38** Now the seller gets an SMS alert.

**0:41** Clasp records the dispatch and sends the buyer a delivery code by SMS.

**0:47** Clasp calls a Truce.

**0:49** A Truce is our engine for dispute resolution.

**0:55** The payout stays on hold.

**0:57** Clasp asks for a fresh photo.

**1:00** The model checks the photo.

**1:02** A separate policy service, AgentSafe, decides what can happen.

**1:07** Asking doesn't give the agent permission to pay it.

**1:10** The proposal has to pass through the policy checks.

**1:14** Only then the executor records a refund.

**1:18** The buyer and seller receive an evidence pack

**1:22** showing the decision and what had happened.

**1:25** It is one order, one approved refund

**1:30** and the record, both sides can check. This is the evidence.

**1:35** Here is the trail in the console, the order, the policy decisions and the refund in the ledger.

**1:41** The agent moves the conversation forward.

**1:44** Every payout needs a valid signed authorization.

**1:49** Clasp, nothing moves until both sides are kept whole.

## Required visual context

- The item is the Zoom H6 recorder: $120 less 10%, with a simulated $108 payment and hold.
- Payments are labelled simulated throughout. The spoken delivery-SMS sentence describes the AWS-accepted send. The accompanying text explicitly says the buyer did not receive that SMS in this take. The seller confirmed the hold-alert SMS, but no SMS screen was captured.
- The fresh photo code is `4UK67C`. The recorded verdict has `code_visible`, `code_matches` and `same_item_as_dispatch` true, confidence `0.95`, and `damage_observed` false. Reported damage must not be presented as a confirmed model finding.
- The buyer selected 20%. The actual decision is `allow`, reason `in_policy`, amount `2160` in USD minor units, under policy `2026.10.25-1`. The ledger contains `DISCOUNT`, `HOLD` and exactly one `REFUND`; there is no release.
- Both SES evidence emails reached the shared demo mailbox in Spam. No supervisor-approval email is substituted.
- The two available iPhones were tested and remain unsupported for RCS. WhatsApp carried the Truce.
- This take includes no above-cap denial, full refund or supervisor approval. Those original narration passages are omitted.
- The seller recording still shows the WhatsApp Invite footer. It proves receipt of the dispatch reply and Truce notice, not a functioning compose box.
- Email/console excerpts are labelled as reviews of the completed order. The signed-authorization explanation reflects the deployed AgentSafe/executor behavior; raw signed tokens are excluded.

The local delivery includes the MP4, SRT, transcript, contact sheet, export checks and exact
source/cut manifest under `demo/recordings/live-edit/`. The three exports with seller-like
filenames are buyer recordings, as corrected by the owner. The separate seller Truce clip
is used for the second phone. Unrelated gallery thumbnails, contact name, account/role ARNs,
mailbox contact details and private authorization fields are masked or excluded.
