"""Workflow task bodies. The Step Functions Lambda and the local runner both call these,
so the console graph and the offline simulation execute the same code.

The order runs on its own: the PSP webhook holds the money, a courier scan records dispatch, the
buyer's code releases it, and AgentSafe decides every Truce resolution. People are messaged, not
asked, except where policy escalates: then the seller approves first and a platform supervisor
decides if the seller rejects or does not answer.

None of these write the ledger. Money moves only via tools.money.propose -> AgentSafe -> executor.
Notices are best effort across channels: a failed send is logged and the next channel is tried;
it never stops the order."""
from __future__ import annotations

import hashlib
import hmac
import html
import json
from typing import Any

from ..common import clock, events
from ..common.config import settings
from ..common.ids import new_code, new_id
from ..common.log import Log
from ..common.media import get_media
from ..common.money import fmt
from ..common.stores import get_stores
from ..notify import router as notify

log = Log("workflow.tasks")

PHASES = ("payment", "dispatch", "delivery", "dispute", "approval")
WAIT_STATUS = {"payment": "PAYMENT_PENDING", "dispatch": None, "delivery": None, "dispute": "DISPUTED", "approval": "ESCALATED"}
DELIVERY_CODE_TTL = 72 * 3600
CHALLENGE_CODE_TTL = 15 * 60
MAX_NOTICES = 20

KIND_LABEL = {"refund": "refund", "partial_refund": "refund", "credit": "store credit", "store_credit": "store credit",
              "replacement": "replacement"}
OUTCOME_LABEL = {"RESOLVED_PARTIAL_REFUND": "partial refund", "RESOLVED_REFUND": "full refund",
                 "RESOLVED_CREDIT": "store credit", "RESOLVED_REPLACEMENT": "replacement item"}
STATUS_WORDS = {"PAYMENT_PENDING": "waiting for payment", "HELD": "paid and held", "DISPATCHED": "on its way",
                "DISPUTED": "in a Truce", "ESCALATED": "waiting for an approval", "RELEASED": "delivered and paid out",
                "RESOLVED_PARTIAL_REFUND": "settled with a partial refund", "RESOLVED_REFUND": "settled with a full refund",
                "RESOLVED_CREDIT": "settled with store credit", "RESOLVED_REPLACEMENT": "settled with a replacement"}


def code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def code_matches(code: str, digest: str) -> bool:
    return hmac.compare_digest(code_hash(code), digest or "")


def short(order_id: str) -> str:
    return order_id[-6:]


def _order(order_id: str) -> dict:
    o = get_stores().get_order(order_id)
    if not o:
        raise KeyError(f"order {order_id} not found")
    return o


def _available(o: dict) -> int:
    from ..agentsafe.policy import available

    return available(o)


def _policy():
    from ..agentsafe.policy import Policy

    return Policy.load()


def email_for(order: dict, who: str) -> str | None:
    """Current address on the party record (a buyer may add one in chat), else the one captured on the order."""
    sub = order.get(f"{who}_sub")
    party = get_stores().party_by_sub(sub) if sub else None
    return (party or {}).get("email") or order.get(f"{who}_email") or None


# --------------------------------------------------------------------------- notices
def record_notice(order_id: str, role: str, channel: str, memo: str) -> None:
    """Remember what Clasp told someone outside the chat, so the agent knows it on their next message."""
    stores = get_stores()
    o = stores.get_order(order_id)
    if not o:
        return
    notices = list(o.get("notices") or [])
    notices.append({"id": new_id("ntc"), "ts": clock.now(), "role": role, "channel": channel, "memo": memo[:500], "seen": False})
    stores.update_order(order_id, {"notices": notices[-MAX_NOTICES:]})


def deliver(order: dict, role: str, attempts: list[dict], memo: str | None = None) -> str | None:
    """Try each channel in turn; return the channel that accepted the message, or None."""
    for attempt in attempts:
        if not attempt.get("to"):
            continue
        try:
            notify.send(order_id=order["order_id"], correlation_id=order.get("correlation_id", ""), **attempt)
        except Exception as e:  # noqa: BLE001 — try the next channel
            log.warn("notify.attempt_failed", order_id=order["order_id"], role=role, channel=attempt["channel"], error=str(e)[:300],
                     correlation_id=order.get("correlation_id") or None)
            continue
        record_notice(order["order_id"], role, attempt["channel"], memo or attempt.get("text", ""))
        return attempt["channel"]
    log.error("notify.undelivered", order_id=order["order_id"], role=role, correlation_id=order.get("correlation_id") or None)
    return None


def _html(text: str) -> str:
    paras = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in text.split("\n\n"))
    return (f"<div style='font-family:-apple-system,system-ui,sans-serif;font-size:15px;line-height:1.5;color:#1b1b1f'>{paras}"
            "<p style='color:#666;font-size:12px'>Clasp prototype · payments are simulated (test mode).</p></div>")


def tell_seller(order: dict, text: str, sms: str, subject: str, rich_card: dict | None = None) -> str | None:
    """One-way seller notice: RCS (SMS fallback inside the pool), then email."""
    email = email_for(order, "seller")
    return deliver(order, "seller", [
        {"channel": "rcs", "to": order.get("seller_phone"), "text": text, "rich_card": rich_card, "fallback_text": sms},
        {"channel": "email", "to": email, "subject": subject, "text": text, "html": _html(text)},
    ], memo=text)


def tell_buyer(order: dict, text: str, *, buttons: list[dict] | None = None, media_url: str | None = None,
               memo: str | None = None, email_subject: str | None = None) -> str | None:
    """Buyer notice in their WhatsApp thread; email as the fallback when a subject is given."""
    attempts = [{"channel": "whatsapp", "to": order.get("buyer_phone"), "text": text, "buttons": buttons, "media_url": media_url}]
    if email_subject:
        attempts.append({"channel": "email", "to": email_for(order, "buyer"), "subject": email_subject, "text": text,
                         "html": _html(text)})
    return deliver(order, "buyer", attempts, memo=memo)


def ladder_buttons(order: dict) -> tuple[str, list[dict]]:
    opts = _policy().truce_options(order)
    return " · ".join(o["display"] for o in opts), [{"id": f"truce:{order['order_id']}:{o['kind']}", "title": o["label"][:20]}
                                                   for o in opts]


# --------------------------------------------------------------------------- waits
def register_token(order_id: str, phase: str, token: str, correlation_id: str = "") -> dict:
    updates: dict[str, Any] = {f"tokens.{phase}": token}
    if WAIT_STATUS.get(phase):
        updates["status"] = WAIT_STATUS[phase]
    stores = get_stores()
    o = stores.get_order(order_id)
    if o is not None and "tokens" not in o:
        updates = {"tokens": {phase: token}, **{k: v for k, v in updates.items() if not k.startswith("tokens.")}}
    out = stores.update_order(order_id, updates)
    log.info("workflow.wait", order_id=order_id, phase=phase, status=out.get("status"), correlation_id=correlation_id or None)
    if phase == "payment":
        events.emit("OrderCreated", out)
    if phase == "dispatch" and settings.courier_mode == "auto":
        from ..courier_mock.courier import auto_pickup

        auto_pickup(order_id)
    return {"order_id": order_id, "phase": phase}


def clear_token(order_id: str, phase: str) -> None:
    get_stores().update_order(order_id, {f"tokens.{phase}": ""})


# --------------------------------------------------------------------------- after payment: book the courier
def book_courier(order_id: str, output: dict | None = None) -> dict:
    """Funds are held (or a replacement was agreed): book a pickup and tell the seller. The seller does nothing
    in the chat; the courier's signed pickup scan records dispatch."""
    o = _order(order_id)
    replacement = o.get("status") == "RESOLVED_REPLACEMENT"
    shipment = {"shipment_id": new_id("shp"), "courier": "mock", "status": "booked", "booked_at": clock.now(),
                "replacement": replacement}
    o = get_stores().update_order(order_id, {"shipment": shipment})
    amount = fmt(int(o.get("held_minor") or o["amount_minor"]), o["currency"])
    item, code = o.get("item_name", "the item"), short(order_id)
    if replacement:
        text = (f"Truce settled for order {code} with a replacement {item}. A courier will collect the replacement from you. "
                f"Your {amount} payout is released once the buyer confirms delivery. No reply needed.")
        tell_seller(o, text, sms=f"Clasp: order {code} settled with a replacement. A courier will collect it. No reply needed.",
                    subject=f"Clasp: courier booked for the replacement on order {code}")
    else:
        text = (f"Payment secured: {amount} for {item} (order {code}) is held by Clasp. It was confirmed directly by the "
                "payment provider (test mode), not a screenshot. A courier will collect the parcel; no reply needed.")
        item_image = (get_stores().get_item(o["item_id"]) or {}).get("image_url")
        tell_seller(o, text,
                    sms=f"Clasp: {amount} payment confirmed (test mode) for order {code}. A courier will collect it. No reply needed.",
                    subject=f"Clasp: payment secured for order {code}",
                    rich_card={"title": "Payment secured", "media_url": item_image,
                               "description": f"{amount} held for {item}, order {code}. Confirmed by the payment provider, "
                                              "not a screenshot. A courier will collect the parcel."})
        buyer_email = email_for(o, "buyer")
        if buyer_email:
            receipt = (f"Payment confirmed for order {code}.\n\nItem: {item}\nPaid: {amount} (test mode: no card was charged)\n"
                       "Clasp holds this payment until you confirm delivery with the one-time code we send you.\n\n"
                       "If anything is wrong when it arrives, tell the Clasp assistant on WhatsApp before you share the code.")
            deliver(o, "buyer", [{"channel": "email", "to": buyer_email, "subject": f"Clasp receipt: {amount} for {item} (order {code})",
                                  "text": receipt, "html": _html(receipt)}], memo=f"Emailed the payment receipt for order {code}.")
        events.emit("OrderHeld", o, seller_notified=True)
    events.emit("CourierBooked", o, shipment_id=shipment["shipment_id"], replacement=replacement)
    return {"shipment_id": shipment["shipment_id"]}


# --------------------------------------------------------------------------- dispatch → delivery
def mark_dispatched(order_id: str, output: dict | None = None) -> dict:
    output = output or {}
    before = _order(order_id)
    replacement = before.get("status") == "RESOLVED_REPLACEMENT"
    o = get_stores().update_order(order_id, {"status": "DISPATCHED", "dispatch_photo_key": output.get("dispatch_photo_key"),
                                             "dispatched_at": clock.now(), "shipment.status": "picked_up"},
                                  expected_status=["HELD", "RESOLVED_REPLACEMENT"])
    photo_url = get_media().url(o["dispatch_photo_key"]) if o.get("dispatch_photo_key") else None
    what = f"replacement {o.get('item_name')}" if replacement else o.get("item_name")
    text = (f"Your {what} (order {short(order_id)}) has been picked up by the courier."
            + (" Here's the parcel photo from pickup." if photo_url else "")
            + " Your delivery code is on its way by text message. Share it with me only once the item is in your hands.")
    tell_buyer(o, text, media_url=photo_url)
    events.emit("OrderDispatched", o, has_dispatch_photo=bool(o.get("dispatch_photo_key")), replacement=replacement)
    return {"status": "DISPATCHED"}


def issue_delivery_code(order_id: str, prefer: str = "sms") -> dict:
    """A fresh one-time delivery code (older ones are superseded). SMS first; WhatsApp if SMS can't be sent."""
    o = _order(order_id)
    stores = get_stores()
    for c in stores.challenges_for_order(order_id):
        if c.get("kind") == "delivery" and c.get("status") == "open":
            stores.update_challenge(order_id, c["challenge_id"], {"status": "superseded"})
    code = new_code(6)
    challenge = {"order_id": order_id, "challenge_id": new_id("chl"), "kind": "delivery", "code_hash": code_hash(code),
                 "created_at": clock.now(), "expires_at": clock.now() + DELIVERY_CODE_TTL, "status": "open",
                 "correlation_id": o.get("correlation_id", "")}
    stores.put_challenge(challenge)
    sc = short(order_id)
    sms = {"channel": "sms", "to": o.get("buyer_phone"),
           "text": f"Clasp: your delivery code for order {sc} is {code}. Send it in WhatsApp only once you have the item. It pays the seller."}
    wa = {"channel": "whatsapp", "to": o.get("buyer_phone"),
          "text": f"Your delivery code for order {sc} is {code}. Send it back to me only once the item is in your hands: "
                  "it releases the seller's payment."}
    channel = None
    for attempt in ([sms, wa] if prefer == "sms" else [wa, sms]):
        channel = deliver(o, "buyer", [attempt], memo=f"Sent the delivery code for order {sc} by {attempt['channel'].upper()} "
                                                      "(the code itself is withheld from you).")
        if channel:
            break
    stores.update_challenge(order_id, challenge["challenge_id"], {"sent_via": channel})
    log.info("delivery_code.sent", order_id=order_id, challenge_id=challenge["challenge_id"], channel=channel,
             correlation_id=o.get("correlation_id") or None)
    events.emit("DeliveryCodeSent", o, challenge_id=challenge["challenge_id"], expires_at=challenge["expires_at"], channel=channel)
    return {"challenge_id": challenge["challenge_id"], "channel": channel}


def send_delivery_code(order_id: str, output: dict | None = None) -> dict:
    return issue_delivery_code(order_id, "sms")


# --------------------------------------------------------------------------- outcomes
def _ledger_lines(order_id: str) -> str:
    names = {"DISCOUNT": "Discount", "HOLD": "Payment held", "RELEASE": "Paid to seller", "REFUND": "Refunded to buyer",
             "CREDIT": "Store credit", "REPLACEMENT": "Replacement"}
    return "\n".join(f"- {names.get(e['type'], e['type'])}: {fmt(int(e['amount_minor']), e['currency'])}"
                     for e in get_stores().ledger_entries(order_id))


def send_receipts(order_id: str, output: dict | None = None) -> dict:
    o = _order(order_id)
    code, item = short(order_id), o.get("item_name")
    paid, released = fmt(int(o["amount_minor"]), o["currency"]), fmt(int(o.get("released_minor") or 0), o["currency"])
    ledger = _ledger_lines(order_id)
    sent = 0
    buyer = email_for(o, "buyer")
    if buyer:
        text = (f"Delivery confirmed for order {code}.\n\nItem: {item}\nYou paid: {paid} (test mode)\n"
                f"The seller has now been paid.\n\nLedger:\n{ledger}\n\nEvery entry was authorized by a signed AgentSafe decision.")
        sent += bool(deliver(o, "buyer", [{"channel": "email", "to": buyer, "subject": f"Clasp: delivery confirmed for order {code}",
                                           "text": text, "html": _html(text)}], memo=f"Emailed the delivery receipt for order {code}."))
    seller_text = (f"Delivery confirmed for order {code} ({item}). {released} has been released to you (test mode).\n\n"
                   f"Ledger:\n{ledger}\n\nEvery entry was authorized by a signed AgentSafe decision.")
    seller = email_for(o, "seller")
    if seller:
        sent += bool(deliver(o, "seller", [{"channel": "email", "to": seller, "subject": f"Clasp: payout released for order {code}",
                                            "text": seller_text, "html": _html(seller_text)}]))
    deliver(o, "seller", [{"channel": "rcs", "to": o.get("seller_phone"), "text": seller_text.split("\n\n")[0],
                           "fallback_text": f"Clasp: delivery confirmed for order {code}. {released} released to you (test mode)."}])
    events.emit("OrderReleased", o, released_minor=o.get("released_minor"), receipts=sent)
    return {"receipts": sent}


def mark_disputed(order_id: str, output: dict | None = None) -> dict:
    output = output or {}
    after_rejection = bool(output.get("after_rejection"))
    before = _order(order_id)
    if after_rejection and before.get("dispute"):  # keep the buyer's own words; note the rejection beside them
        updates: dict[str, Any] = {"status": "DISPUTED", "dispute.reopened": True, "dispute.rejected_at": clock.now()}
    else:
        updates = {"status": "DISPUTED", "dispute": {"reason": output.get("reason", "") if not after_rejection else "",
                                                     "opened_at": clock.now(), "reopened": after_rejection}}
    o = get_stores().update_order(order_id, updates, expected_status=["DISPATCHED", "DELIVERED", "ESCALATED", "DISPUTED"])
    code = short(order_id)
    if after_rejection:
        esc = before.get("escalation") or {}
        asked = fmt(int(esc.get("amount_minor") or 0), esc.get("currency", o["currency"]))
        _, buttons = ladder_buttons(o)
        tell_buyer(o, f"A Clasp supervisor reviewed your request for order {code} and couldn't approve the {asked} "
                      f"{KIND_LABEL.get(esc.get('kind', 'refund'), 'refund')}. Here's what I can still offer:", buttons=buttons)
    else:
        text = (f"The buyer reported a problem with order {code}. Clasp has called a Truce: your payout stays held while "
                "evidence is collected. Nothing to do yet; you'll only be asked if a decision needs you.")
        tell_seller(o, text, sms=f"Clasp: buyer reported a problem with order {code}. Payout stays on hold during the Truce. Nothing to do yet.",
                    subject=f"Clasp: Truce called on order {code}")
    events.emit("TruceOpened", o, reopened=after_rejection)
    return {"status": "DISPUTED"}


def mark_escalated(order_id: str, output: dict | None = None) -> dict:
    """The agent already told the buyer in the same turn; this parks the order for the first approver."""
    output = output or {}
    first = _policy().stages[0]["role"]
    kind = output.get("kind", "refund")
    o = get_stores().update_order(order_id, {"status": "ESCALATED", "escalation": {
        "decision_id": output.get("decision_id"), "amount_minor": output.get("amount_minor"),
        "currency": output.get("currency", "USD"), "kind": kind,
        "action": output.get("action") or {"store_credit": "credit", "replacement": "replacement"}.get(kind, "refund"),
        "reason": output.get("reason", ""), "stage": first, "opened_at": clock.now(), "history": []}},
        expected_status=["DISPUTED"])
    events.emit("OrderEscalated", o, requested_minor=output.get("amount_minor"), decision_id=output.get("decision_id"), stage=first)
    return {"status": "ESCALATED", "stage": first}


def why_lines(reason: str, order: dict) -> list[str]:
    """Plain-language reasons for an escalation, from AgentSafe's reason codes."""
    rules = _policy().actions.get("refund", {})
    cap = fmt(int(rules.get("auto_cap_minor", 0)), order.get("currency", "USD"))
    out = []
    if "above_auto_cap" in reason:
        out.append(f"It is above the automatic limit ({cap} or {rules.get('auto_cap_percent_of_order', 0)}% of the order).")
    if "repeated_denials" in reason:
        out.append("The buyer's conversation had several requests denied by policy on this order.")
    if "inconclusive_evidence (confidence" in reason:
        out.append("The evidence check is not confident about the live photo.")
    if "no damage visible" in reason:
        out.append("The live photo does not clearly show damage.")
    if "does not match the dispatch photo" in reason:
        out.append("The item in the live photo does not clearly match the pickup photo.")
    if "policy_version_change" in reason:
        out.append("Clasp's policy changed after this payment was held.")
    if "off_ladder" in reason:
        out.append("The amount is not one of the standard Truce options.")
    if "decionis_escalate" in reason:
        out.append("Decionis AgentSafe held the request for human review.")
    return out or ["Policy requires a human decision for this request."]


def verdict_lines(order_id: str) -> str:
    verdicts = [c for c in get_stores().challenges_for_order(order_id) if c.get("kind") == "dispute" and c.get("verdict")]
    if not verdicts:
        return "No live evidence has been checked yet."
    v = verdicts[-1]["verdict"]

    def yn(b: Any) -> str:
        return "yes" if b else "no"

    lines = [f"- Challenge code visible: {yn(v.get('code_visible'))}", f"- Code matches: {yn(v.get('code_matches'))}",
             f"- Same item as the pickup photo: {yn(v.get('same_item_as_dispatch'))}",
             f"- Damage visible: {yn(v.get('damage_observed'))}", f"- Confidence: {float(v.get('confidence') or 0):.2f}"]
    if v.get("notes"):
        lines.append(f"- Notes: {v['notes']}")
    return "\n".join(lines)


def send_approval_email(order_id: str, output: dict | None = None) -> dict:
    from ..agentsafe import adapter as agentsafe

    o = _order(order_id)
    p = _policy()
    esc = o.get("escalation") or {}
    stage = esc.get("stage") or p.stages[0]["role"]
    amount, currency = int(esc.get("amount_minor") or 0), esc.get("currency", "USD")
    approver = email_for(o, "seller") if stage == "seller" else settings.supervisor_email
    if stage == "seller" and not approver:  # no way to reach the seller: go straight to the next stage
        log.warn("approval.seller_unreachable", order_id=order_id)
        return next_approval_stage(order_id, {"seller_outcome": "unreachable"})
    links = agentsafe.approval_links(order_id=order_id, decision_id=esc.get("decision_id", "-"), amount_minor=amount,
                                     currency=currency, approver=approver, stage=stage)
    base = settings.api_base_url
    approve_url, reject_url = f"{base}/approvals?token={links['approve']}", f"{base}/approvals?token={links['reject']}"
    hours = int(p.stage(stage)["ttl_seconds"]) // 3600
    code, kind = short(order_id), KIND_LABEL.get(esc.get("kind", "refund"), "refund")
    what = f"a {kind} of {fmt(amount, currency)}" if amount else f"a {kind}"
    held = fmt(_available(o), currency)
    rest = fmt(max(_available(o) - amount, 0), currency)
    reason = (o.get("dispute") or {}).get("reason") or "-"
    whys = why_lines(esc.get("reason", ""), o)
    replacement = esc.get("action") == "replacement"
    if stage == "seller":
        intro = (f"The buyer of order {code} ({o.get('item_name')}, {held} held) asked for {what}. "
                 "Clasp's policy needs your approval because:")
        if replacement:
            consequence = "If you approve, a courier collects a replacement from you and your payout is released once it's delivered."
        elif amount >= _available(o):
            consequence = f"If you approve, the buyer gets the full {fmt(amount, currency)} back and nothing is paid out to you for this order."
        else:
            consequence = f"If you approve, {fmt(amount, currency)} goes to the buyer from the held payment and {rest} is paid to you."
        after = f"If you reject it or don't answer within {hours} hours, a Clasp supervisor decides."
    else:
        seller_outcome = {"rejected": "The seller rejected it.", "timeout": "The seller didn't answer in time.",
                          "unreachable": "The seller has no email address on file."}.get(esc.get("seller_outcome", ""), "")
        intro = f"Order {code} ({o.get('item_name')}, {held} held): the buyer asked for {what}. {seller_outcome} It needs a decision because:"
        consequence = (f"Approving gives the buyer {what}." if replacement
                       else f"Approving gives the buyer {fmt(amount, currency)} from the held payment"
                            + (f"; {rest} goes to the seller." if amount < _available(o) else "; nothing is paid out to the seller."))
        after = f"Rejecting returns the buyer to the standard Truce options. The links expire in {hours} hours."
    evidence = verdict_lines(order_id)
    text = (f"{intro}\n" + "\n".join(f"- {w}" for w in whys) + f"\n\nWhat the buyer reported: \"{reason}\"\n\n"
            f"Evidence check of the buyer's live photo:\n{evidence}\n\n{consequence} {after}\n\n"
            f"Approve: {approve_url}\n\nReject: {reject_url}\n\n"
            "Each link opens a confirmation page first and works once. Approving mints a fresh AgentSafe authorization "
            "bound to this order and amount.")
    body_html = (f"<p>{html.escape(intro)}</p><ul>{''.join(f'<li>{html.escape(w)}</li>' for w in whys)}</ul>"
                 f"<p>What the buyer reported: <i>“{html.escape(reason)}”</i></p><p>Evidence check of the buyer's live photo:<br>"
                 f"{html.escape(evidence).replace(chr(10), '<br>')}</p><p>{html.escape(consequence)} {html.escape(after)}</p>"
                 f"<p><a href='{html.escape(approve_url)}' style='padding:10px 16px;background:#0a7d3e;color:#fff;border-radius:8px;"
                 f"text-decoration:none'>Review and approve</a>&nbsp;&nbsp;<a href='{html.escape(reject_url)}' style='padding:10px 16px;"
                 "background:#b00020;color:#fff;border-radius:8px;text-decoration:none'>Review and reject</a></p>"
                 "<p style='color:#666;font-size:12px'>Each link opens a confirmation page first and works once. Approving mints a fresh "
                 "AgentSafe authorization bound to this order and amount. Payments are simulated (test mode).</p>")
    subject = (f"Clasp: approve {what} for order {code}?" if stage == "seller"
               else f"[Clasp supervisor] Decide {what} for order {code}")
    mid = notify.send(channel="email", to=approver, subject=subject, text=text, html=body_html, order_id=order_id,
                      correlation_id=o.get("correlation_id", ""))
    if stage == "seller":
        deliver(o, "seller", [{"channel": "rcs", "to": o.get("seller_phone"),
                               "text": f"Approval needed for order {code}: {what}. Please check your email to approve or reject.",
                               "fallback_text": f"Clasp: approval needed for order {code}: {what}. Check your email. No reply needed here."}])
    events.emit("ApprovalRequested", o, stage=stage, requested_minor=amount)
    return {"message_id": mid, "stage": stage, "approve_url": approve_url, "reject_url": reject_url}


def next_approval_stage(order_id: str, output: dict | None = None) -> dict:
    """The seller rejected or did not answer: hand the decision to the next approver (the platform supervisor)."""
    output = output or {}
    o = _order(order_id)
    p = _policy()
    esc = o.get("escalation") or {}
    current = esc.get("stage") or p.stages[0]["role"]
    nxt = p.next_stage(current)
    if nxt is None:
        return {"stage": current}
    error = (output.get("error") or {}).get("Error") if isinstance(output.get("error"), dict) else output.get("Error")
    seller_outcome = output.get("seller_outcome") or ("rejected" if error == "ApprovalRejected" else "timeout")
    history = list(esc.get("history") or []) + [{"stage": current, "outcome": seller_outcome, "at": clock.now()}]
    o = get_stores().update_order(order_id, {"escalation.stage": nxt["role"], "escalation.seller_outcome": seller_outcome,
                                             "escalation.history": history})
    amount = fmt(int(esc.get("amount_minor") or 0), esc.get("currency", o["currency"]))
    lead = "The seller didn't approve" if seller_outcome == "rejected" else "The seller hasn't answered about"
    tell_buyer(o, f"{lead} the {amount} {KIND_LABEL.get(esc.get('kind', 'refund'), 'refund')} for order {short(order_id)}, "
                  "so a Clasp supervisor is reviewing it now. I'll message you here with the answer.")
    return send_approval_email(order_id)


def notify_outcome(order_id: str, output: dict | None = None) -> dict:
    """A human approved and the executor moved the money: close the loop in the buyer's chat."""
    o = _order(order_id)
    esc = o.get("escalation") or {}
    who = "the seller" if esc.get("stage") == "seller" else "a Clasp supervisor"
    status, code = o.get("status", ""), short(order_id)
    if status == "RESOLVED_REPLACEMENT":
        text = f"Good news: {who} approved a replacement for order {code}. I'll let you know when the courier picks it up."
    else:
        moved = int(o.get("refunded_minor") or 0) or int(o.get("credited_minor") or 0)
        kind = "store credit" if status == "RESOLVED_CREDIT" else "refund"
        text = f"Good news: {who} approved your {kind} of {fmt(moved, o['currency'])} for order {code}. It has been issued (test mode)."
    if email_for(o, "buyer"):
        text += " The evidence pack is on its way to your email."
    tell_buyer(o, text, email_subject=f"Clasp: your Truce on order {code} is settled")
    return {"notified": True}


class DecionisUnavailable(Exception):
    """Raised so Step Functions retries the task: an enforcing gateway that can't answer moves nothing."""


def settle_remainder(order_id: str, output: dict | None = None) -> dict:
    """Keep both sides whole: after a partial refund or store credit, the rest of the hold goes to the seller.
    It is still a proposal: AgentSafe decides as system:workflow and the executor writes it."""
    from ..tools.money import propose

    o = _order(order_id)
    status, code, cur = o.get("status", ""), short(order_id), o["currency"]
    left = _available(o)
    to_buyer = fmt(int(o.get("refunded_minor") or 0) or int(o.get("credited_minor") or 0), cur)
    result: dict[str, Any] = {"settled": False}
    if status in {"RESOLVED_PARTIAL_REFUND", "RESOLVED_CREDIT"} and left > 0:
        resolution = next((e for e in reversed(get_stores().ledger_entries(order_id)) if e["type"] in {"REFUND", "CREDIT"}), {})
        result = propose("settle", order_id=order_id, amount_minor=left, currency=cur, actor_sub="system:workflow",
                         evidence={"truce_resolved": True, "resolution_decision_id": resolution.get("decision_id")},
                         metadata={"resolution": status}, correlation_id=o.get("correlation_id", ""))
        result["settled"] = bool(result.get("executed"))
        if str(result.get("reason", "")).startswith("decionis_unavailable"):
            raise DecionisUnavailable(result["reason"])
        kind = "store credit" if status == "RESOLVED_CREDIT" else "partial refund"
        if result["settled"]:
            text = (f"Truce settled for order {code}: {kind} of {to_buyer} to the buyer. The remaining {fmt(left, cur)} "
                    "has been released to you (test mode).")
            sms = f"Clasp: Truce settled for order {code}. {to_buyer} to the buyer; {fmt(left, cur)} released to you (test mode)."
        else:
            log.error("settle.not_executed", order_id=order_id, decision=result.get("decision"), reason=result.get("reason"))
            text = f"Truce settled for order {code}: {kind} of {to_buyer} to the buyer. Your remaining payout is being reviewed."
            sms = f"Clasp: Truce settled for order {code}. {to_buyer} to the buyer; the rest of your payout is under review."
        tell_seller(o, text, sms=sms, subject=f"Clasp: Truce settled for order {code}")
        events.emit("OrderSettled", get_stores().get_order(order_id) or o, released_minor=left if result["settled"] else 0)
    elif status == "RESOLVED_REFUND":
        tell_seller(o, f"Truce settled for order {code}: the buyer received a full refund of {to_buyer}. Nothing is paid out for this order.",
                    sms=f"Clasp: Truce settled for order {code} with a full refund to the buyer. No payout for this order.",
                    subject=f"Clasp: Truce settled for order {code}")
    return {k: v for k, v in result.items() if k in {"settled", "decision", "reason", "decision_id", "status"}}


def _decided_by(order_id: str) -> str:
    allows = [d for d in get_stores().decisions_for_order(order_id)
              if d.get("decision") == "allow" and d.get("action") in {"refund", "credit", "replacement"}]
    reason = (allows[-1].get("reason") or "") if allows else ""
    if reason.startswith("seller_approved"):
        return "the seller"
    if reason.startswith("supervisor_approved"):
        return "a Clasp supervisor"
    return "policy, automatically"


def send_evidence_pack(order_id: str, output: dict | None = None) -> dict:
    o = _order(order_id)
    stores = get_stores()
    ledger = stores.ledger_entries(order_id)
    decisions = stores.decisions_for_order(order_id)
    challenges = stores.challenges_for_order(order_id)
    pack = {"order": {k: o.get(k) for k in ("order_id", "item_name", "amount_minor", "currency", "status", "resolution", "dispute",
                                            "escalation", "shipment")},
            "ledger": ledger,
            "decisions": [{k: d.get(k) for k in ("decision_id", "action", "amount_minor", "decision", "reason", "message", "engine",
                                                 "policy_version", "ts")} for d in decisions],
            "evidence": [{k: c.get(k) for k in ("challenge_id", "kind", "status", "verdict", "created_at")} for c in challenges]}
    body = json.dumps(pack, indent=2, default=str)
    status, code, cur = o.get("status", ""), short(order_id), o["currency"]
    label = OUTCOME_LABEL.get(status, STATUS_WORDS.get(status, status.lower()))
    moved = int(o.get("refunded_minor") or 0) or int(o.get("credited_minor") or 0)
    outcome = f"{label} ({fmt(moved, cur)})" if moved else label
    denied = sum(1 for d in decisions if d.get("decision") == "deny")
    text = (f"Truce settled for order {code} ({o.get('item_name')}).\n\nOutcome: {outcome}\nDecided by: {_decided_by(order_id)}\n"
            f"What the buyer reported: \"{(o.get('dispute') or {}).get('reason') or '-'}\"\n\nEvidence check:\n{verdict_lines(order_id)}\n\n"
            f"Ledger:\n{_ledger_lines(order_id)}\n\nAgentSafe made {len(decisions)} decisions on this order ({denied} denied). "
            "Every one, with its reason and policy version, is in the attached evidence pack, together with the ledger "
            "and the evidence verdicts. Payments in this prototype are simulated.")
    sent = 0
    for who in ("buyer", "seller"):
        email = email_for(o, who)
        if email:
            sent += bool(deliver(o, who, [{"channel": "email", "to": email, "subject": f"Clasp: Truce settled for order {code}: {outcome}",
                                           "text": text, "html": _html(text),
                                           "attachments": [{"filename": f"clasp-evidence-{code}.json", "content_type": "application/json",
                                                            "data": body.encode()}]}],
                                 memo=f"Emailed the evidence pack for order {code}."))
    events.emit("OrderResolved", o, outcome=status, refunded_minor=o.get("refunded_minor"), credited_minor=o.get("credited_minor"),
                decisions=len(decisions), evidence_packs=sent)
    return {"evidence_packs": sent}


STEPS = {
    "register_token": None,  # handled in handler (needs the task token)
    "book_courier": book_courier,
    "mark_dispatched": mark_dispatched,
    "send_delivery_code": send_delivery_code,
    "send_receipts": send_receipts,
    "mark_disputed": mark_disputed,
    "mark_escalated": mark_escalated,
    "send_approval_email": send_approval_email,
    "next_approval_stage": next_approval_stage,
    "notify_outcome": notify_outcome,
    "settle_remainder": settle_remainder,
    "send_evidence_pack": send_evidence_pack,
}
