# Clasp — conversational narration

Read warmly, as though you are showing the product to one person. Aim for about 125–135 words
per minute and leave the marked pauses for the on-screen actions. The timings are editing
guides, not words to read aloud. Keep the final video under three minutes, including the
three-second typewritten Clasp opening and five-second Clasp closing. Use real phone,
email and console captures through the body, with a small square narrator at bottom-left.

This script follows the completed partial-refund take for order `27e389`. Match each spoken
claim to the actual phone, email or console evidence. Payments are simulated. The selected
take did not include an above-cap denial, supervisor approval or received buyer delivery SMS.

## 0:00–0:20 — Introduce the problem

Buying something through chat is easy. Trusting what happens next is the hard part.

Clasp helps a buyer and seller complete a purchase, and work things out if something goes wrong.

We're using WhatsApp, SMS and email. RCS is configured, but both available iPhones are unsupported.

## 0:20–0:50 — Buy and confirm the hold

I'm the buyer here, and I ask about the Zoom H6 recorder and a ten percent discount.

Claude, on Amazon Bedrock, checks the live catalogue through AgentCore and creates a checkout link.

For this prototype, payments are simulated. The order is only marked as held after the payment
service sends a signed confirmation.

*[Pause while the checkout completes.]*

## 0:50–1:10 — Seller dispatch

Now the seller gets an SMS alert. The seller sends the order number with a photo of the
recorder in WhatsApp.

Clasp records the dispatch. AWS accepts the delivery-code SMS, but it does not arrive on
the buyer's phone in this take.

## 1:10–1:25 — Something goes wrong

I report a damaged battery cover, and Clasp opens a Truce while the payout stays on hold.

## 1:25–2:05 — Evidence and the policy boundary

Clasp asks for a fresh photo with a one-time code beside the recorder. The model matches the
code and item, but does not confirm damage.
A separate policy service, AgentSafe, decides what can happen.

I choose the twenty percent partial refund: twenty-one dollars and sixty cents.

AgentSafe checks my identity, the order state, the evidence, the held amount and the refund limits.

*[Pause on the actual `allow` decision and amount.]*

## 2:05–2:35 — Authorized refund

It returns allow. The executor verifies the signed authorization before adding one simulated
refund to the ledger.

The buyer and seller receive their evidence packs by email.

It's one order, one authorized refund, and a record both sides can check.

## 2:40–3:00 — Show the evidence

And here's the trail in the console: the order, the policy decisions, and the refund in the ledger.

The agent moves the conversation forward. Every payout still needs a valid, signed authorization.

Clasp: nothing moves until both sides are kept whole.

---

The saved photo verdict is `code_visible: true`, `code_matches: true`,
`same_item_as_dispatch: true`, `damage_observed: false`, confidence `0.95`. The damage is the
buyer's report, not a verified model finding. The refund decision is `allow`, reason
`in_policy`, amount `2160` in USD minor units. The completed ledger contains `DISCOUNT`,
`HOLD` and exactly one `REFUND`; there is no release entry. Show the real decision and ledger.

The seller confirmed SMS receipt, but its footage still needs a coverage check. AWS acceptance
of the buyer's delivery-code SMS is not proof of delivery. Both SES evidence emails arrived in
Spam. Use the received emails and attachment; do not substitute a supervisor approval email.

The supplied narration has already been cropped and cleaned. Record the eight replacement
passages in the local pickup sheet with the same framing and sound, then match their timing
to the real workflow captures. Keep the original recording and its earlier edit intact.
The older `demo/compose_demo.py` layout does not include these bookends or the narrator inset.
