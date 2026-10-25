# Clasp — conversational narration

Read warmly, as though you are showing the product to one person. Aim for about 125–135 words
per minute and leave the marked pauses for the on-screen actions. The timings are editing
guides, not words to read aloud. Keep the final audio track at 180 seconds, including pauses.

This is a script for the planned live recording. Match each claim to the captured result;
the WhatsApp workflow still needs to run successfully before the video can be submitted.

## 0:00–0:20 — Introduce the problem

Buying something through chat is easy. Trusting what happens next is the hard part.

Clasp helps a buyer and seller complete a purchase, and work things out if something goes wrong.

Today we're using WhatsApp, SMS and email. RCS is configured, but handset delivery is still untested.

## 0:20–0:50 — Buy and confirm the hold

I'm the buyer here. I ask about the camera bag and a ten-percent discount.

Claude, on Amazon Bedrock, checks the live catalogue through AgentCore and creates a checkout link.

For this prototype, payments are simulated. The order is only marked as held after the payment
service sends a signed confirmation.

*[Pause while the checkout completes.]*

## 0:50–1:10 — Seller dispatch

Now the seller gets an SMS alert. They open WhatsApp and send the order number with a photo
of the packed bag.

Clasp records the dispatch, and sends the buyer a delivery code by SMS.

## 1:10–1:25 — Something goes wrong

Entering that code would release the payout. But the strap has arrived torn, so I leave it
unused and report the damage.

Clasp calls a Truce.

## 1:25–2:05 — Evidence and the policy boundary

The payout stays on hold.

Clasp asks for a fresh photo with a one-time code beside the bag. The model checks the photo.
A separate policy service, AgentSafe, decides what can happen.

Then I ask for a thousand-dollar refund.

*[Pause on the response.]*

Asking doesn't give the agent permission to pay it. The proposal still has to pass the policy checks.

Instead, I ask for the full amount I paid. That's above the automatic refund limit, so it needs a supervisor.

## 2:05–2:40 — Supervisor approval

Over here, the supervisor gets an email with the amount, the reason, and the evidence.

They approve this specific refund using a signed link.

*[Pause on the approval result.]*

Only then can the executor record the refund. The buyer and seller receive an evidence pack
showing the decision and what happened.

It's one order, one approved refund, and a record both sides can check.

## 2:40–3:00 — Show the evidence

And here's the trail in the console: the order, the policy decisions, and the refund in the ledger.

The agent moves the conversation forward. Every payout still needs a valid, signed authorization.

Clasp: nothing moves until both sides are kept whole.

---

If the live log shows an actual AgentSafe denial for the $1,000 proposal, the boundary line can be:
“AgentSafe refuses it, and the ledger hasn't changed.” If the model declines before calling the
tool, keep the main script's wording; do not present that as a policy-service decision.

Record the narration after checking the captured sequence. Save it as
`demo/recordings/narration.m4a` or `.wav`; pass that actual path to `demo/compose_demo.py`.
