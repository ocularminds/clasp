# Clasp compensation and dispute policy (version 2026.10.25-1)

## Overview
Clasp holds the buyer's payment from purchase to confirmed delivery. The seller is paid only when the buyer confirms delivery with a one-time code. If something goes wrong, Clasp calls a Truce: the payout stays held, evidence is collected, and compensation is chosen from a fixed ladder. The assistant can propose a discount, hold, release, refund, store credit or replacement, but it has no authority to move money. Every proposal is decided by AgentSafe, the policy service, which allows it, denies it with a reason, or escalates it to a human supervisor.

## Who can act on an order
Only the buyer who placed an order can ask for a discount, confirm delivery or open a dispute on it, and only its seller can record dispatch. Identity comes from the verified phone number the message was sent from, never from what the message says. A payment is confirmed only by the payment provider's signed webhook; screenshots and promises are not payment.

## Discounts
The assistant may offer a discount of up to 10% of the list price while the order is unpaid (states: DRAFT, PAYMENT_PENDING). Stacked discounts count together: the total may never exceed 10%.

## Payment hold
When the provider confirms payment, the full order amount is placed on hold (it must equal the order amount; required evidence: psp_verified, psp_event_id). The seller is told that funds are secured by the provider and may dispatch.

## Delivery and release
After the seller records dispatch with a photo, the buyer receives a one-time delivery code by SMS. Giving that code to the assistant releases the held amount to the seller (states: DISPATCHED, DELIVERED; required evidence: delivery_code_verified). The release always equals the held amount. A wrong code releases nothing.

## Truce: how a dispute works
If the buyer reports damage or a wrong item after dispatch, Clasp opens a Truce. The payout stays held. The buyer gets a fresh one-time challenge code and must send one live photo showing the code written next to the item. The assistant compares it with the seller's dispatch photo and reports whether the code is visible and matches, whether it is the same item, and whether damage is visible. That report is evidence only; AgentSafe decides. No refund, credit or replacement is possible until the challenge is verified (required evidence: challenge_verified).

## Compensation ladder (what the assistant may offer)
- 20% partial refund (20% of the order), action: refund
- Replacement item, action: replacement
- 30% store credit (30% of the order), action: credit
At most three options are offered as reply buttons. The buyer may also ask for a different amount; the assistant submits it and relays AgentSafe's decision.

## Refund limits
A refund is allowed automatically when the challenge is verified, the amount is within what is held, and the amount is at most $50.00 and at most 30% of the order value. Anything above that is escalated to a supervisor (escalate). A refund can never exceed the amount held for the order. Each buyer may receive at most 1 refund in any 30-day period; further refund proposals are denied for velocity.

## Store credit and replacement
Store credit follows the same limits as refunds (automatic up to $50.00 and 30% of the order, verified evidence required, above that escalated). A replacement item moves no money (zero amount) and also requires verified evidence. Both are possible only in a Truce (states: DISPUTED, ESCALATED).

## Supervisor approval
Above-cap refunds and credits are sent to a supervisor by email with signed Approve and Reject links that expire after 48 hours. An approval is bound to one order, one amount and one decision; it cannot be reused or edited. If the supervisor rejects, the order returns to the Truce and the ladder options remain available. While an order is escalated, nothing else moves on it.

## Authorizations
Every allowed action is a signed authorization (ES256) that lives for 90 seconds (never more than 120), can be used once, and matches exactly one action, order, amount, currency and actor. The executor refuses anything else, so a denial cannot be talked around and a decision cannot be replayed.

## What never happens
The assistant never moves money on its own, never accepts card or bank details in chat, never treats a screenshot as payment, never retries a denied amount, and never acts for anyone other than the verified sender.
