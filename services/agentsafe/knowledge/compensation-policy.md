# Clasp compensation and dispute policy (version 2026.10.27-1)

## Overview
Clasp holds the buyer's payment from purchase to confirmed delivery. The seller is paid only when the buyer confirms delivery with a one-time code. If something goes wrong, Clasp calls a Truce: the payout stays held, evidence is collected, and compensation is chosen from a fixed ladder. The assistant can propose a discount, refund, store credit or replacement, but it has no authority to move money. Every proposal is decided by Decionis AgentSafe, the policy service, which allows it, denies it with a reason, or escalates it to a human: the seller first, then a Clasp supervisor. Sellers are never asked to do anything in the chat; they are contacted only when a decision needs them.

## Who can act on an order
Only the buyer who placed an order can ask for a discount, confirm delivery or open a dispute on it. Dispatch is recorded by the courier's signed pickup scan, not by a message. Identity comes from the verified phone number the message was sent from, never from what the message says. A payment is confirmed only by the payment provider's signed webhook; screenshots and promises are not payment.

## Discounts
The assistant may offer a discount of up to 10% of the list price while the order is unpaid (states: DRAFT, PAYMENT_PENDING). Stacked discounts count together: the total may never exceed 10%.

## Payment hold
When the provider confirms payment, the full order amount is placed on hold (it must equal the order amount; required evidence: psp_verified, psp_event_id). The seller is told that funds are secured by the provider, and a courier pickup is booked.

## Delivery and release
When the courier scans the pickup with a parcel photo, the buyer receives a one-time delivery code by text message (or in the WhatsApp chat if a text can't be delivered). Giving that code to the assistant releases the held amount to the seller (states: DISPATCHED, DELIVERED; required evidence: delivery_code_verified). The release always equals the held amount. A wrong code releases nothing.

## Truce: how a dispute works
If the buyer reports damage or a wrong item after dispatch, Clasp opens a Truce. The payout stays held. The buyer gets a fresh one-time challenge code and must send one live photo, or a video under 30 seconds, showing the code written next to the item within 15 minutes. The assistant compares it with the courier's pickup photo and reports whether the code is visible and matches, whether it is the same item, and whether damage is visible. That report is evidence only; AgentSafe decides. No refund, credit or replacement is possible until the challenge is verified (required evidence: challenge_verified).

## Compensation ladder (what the assistant may offer)
- 20% partial refund (20% of the order), action: refund
- Replacement item, action: replacement
- 30% store credit (30% of the order), action: credit
At most three options are offered as reply buttons. The buyer may also ask for a different amount; the assistant submits it and relays AgentSafe's decision.

## Refund limits
A refund is allowed automatically when the challenge is verified, the amount is within what is held, and the amount is at most $50.00 and at most 30% of the order value. Anything above that is escalated for human approval (escalate). A refund can never exceed the amount held for the order. Each buyer may receive at most 1 refund in any 30-day period; further refund proposals are denied for velocity.

## Store credit and replacement
Store credit follows the same limits as refunds (automatic up to $50.00 and 30% of the order, verified evidence required, above that escalated). A replacement item moves no money (zero amount) and also requires verified evidence. Both are possible only in a Truce (states: DISPUTED, ESCALATED).

## Policy drift
Some requests are within the limits but still go to a human instead of executing automatically:
- repeated denials: the order already has 3 or more denied proposals;
- inconclusive evidence: confidence below 0.8, no damage visible, or the item does not match the pickup photo;
- a policy change: the policy in force differs from the one that authorized the payment hold;
- an amount that is not on the compensation ladder.

## Human approval: seller first, then a supervisor
Escalated refunds, credits and replacements go first to the seller (signed email links valid 24 hours), then to a supervisor (signed email links valid 48 hours). If the seller rejects or does not answer in time, a Clasp supervisor decides. Each link opens a confirmation page first. An approval is bound to one order, one amount, one decision and one stage; it cannot be reused or edited. If the supervisor rejects, the order returns to the Truce and the ladder options remain available. While an order is escalated, nothing else moves on it.

## Settlement
After a partial refund or store credit, the rest of the held payment is released to the seller automatically, as its own AgentSafe decision. After a replacement, a courier collects the replacement and the payout is released when the buyer confirms its delivery. After a full refund, nothing is paid out.

## Authorizations
Every allowed action is a signed authorization (ES256) that lives for 90 seconds (never more than 120), can be used once, and matches exactly one action, order, amount, currency and actor. The executor refuses anything else, so a denial cannot be talked around and a decision cannot be replayed.

## What never happens
The assistant never moves money on its own, never accepts card or bank details in chat, never treats a screenshot as payment, never retries a denied amount, and never acts for anyone other than the verified sender.
