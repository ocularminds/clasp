"""Workflow task bodies. The Step Functions Lambda and the local runner both call these,
so the console graph and the offline simulation execute the same code.

None of these move money. Money moves only via tools.money.propose -> AgentSafe -> executor."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from ..common import clock
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


def code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def code_matches(code: str, digest: str) -> bool:
    return hmac.compare_digest(code_hash(code), digest or "")


def _order(order_id: str) -> dict:
    o = get_stores().get_order(order_id)
    if not o:
        raise KeyError(f"order {order_id} not found")
    return o


def _party_contact(order: dict, who: str) -> dict:
    return {"phone": order.get(f"{who}_phone"), "email": order.get(f"{who}_email"), "name": order.get(f"{who}_name", who)}


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
    return {"order_id": order_id, "phase": phase}


def clear_token(order_id: str, phase: str) -> None:
    get_stores().update_order(order_id, {f"tokens.{phase}": ""})


# --------------------------------------------------------------------------- after payment
def notify_seller_funds_held(order_id: str, output: dict | None = None) -> dict:
    o = _order(order_id)
    amount = fmt(int(o.get("held_minor") or o["amount_minor"]), o["currency"])
    text = (f"Funds secured by provider: {amount} for '{o.get('item_name')}' is held by Clasp for order {order_id}. "
            f"Dispatch the item and reply with a photo of the packed parcel to start delivery.")
    item_image = (get_stores().get_item(o["item_id"]) or {}).get("image_url")
    mid = notify.send(
        channel="rcs", to=o["seller_phone"], text=text, correlation_id=o.get("correlation_id", ""), order_id=order_id,
        rich_card={"title": "Funds secured by provider", "description": f"{amount} held for {o.get('item_name')}. "
                   "Confirmed by the payment provider's webhook, not a screenshot. Upload a dispatch photo to continue.",
                   "media_url": item_image, "suggestions": [{"text": "Upload dispatch photo", "postback": f"dispatch:{order_id}"}]},
        fallback_text=text,
    )
    return {"message_id": mid}


# --------------------------------------------------------------------------- dispatch → delivery
def mark_dispatched(order_id: str, output: dict | None = None) -> dict:
    output = output or {}
    o = get_stores().update_order(order_id, {"status": "DISPATCHED", "dispatch_photo_key": output.get("dispatch_photo_key"),
                                             "dispatched_at": clock.now()}, expected_status=["HELD"])
    photo_url = get_media().url(o["dispatch_photo_key"]) if o.get("dispatch_photo_key") else None
    notify.send(channel="whatsapp", to=o["buyer_phone"], order_id=order_id, correlation_id=o.get("correlation_id", ""),
                text=f"Your '{o.get('item_name')}' has been dispatched. Here is the seller's dispatch photo. "
                     "A one-time delivery code is on its way by SMS; share it with me only when the item is in your hands.",
                media_url=photo_url)
    return {"status": "DISPATCHED"}


def send_delivery_code(order_id: str, output: dict | None = None) -> dict:
    o = _order(order_id)
    code = new_code(6)
    challenge = {"order_id": order_id, "challenge_id": new_id("chl"), "kind": "delivery", "code_hash": code_hash(code),
                 "created_at": clock.now(), "expires_at": clock.now() + DELIVERY_CODE_TTL, "status": "open",
                 "correlation_id": o.get("correlation_id", "")}
    get_stores().put_challenge(challenge)
    notify.send(channel="sms", to=o["buyer_phone"], order_id=order_id, correlation_id=o.get("correlation_id", ""),
                text=f"Clasp delivery code for order {order_id[-6:]}: {code}. Give it to the Clasp assistant on WhatsApp "
                     "only once the item is in your hands. It releases the seller's payout.")
    log.info("delivery_code.sent", order_id=order_id, challenge_id=challenge["challenge_id"],
             correlation_id=o.get("correlation_id") or None)
    return {"challenge_id": challenge["challenge_id"]}


# --------------------------------------------------------------------------- outcomes
def send_receipts(order_id: str, output: dict | None = None) -> dict:
    o = _order(order_id)
    ledger = get_stores().ledger_entries(order_id)
    lines = "\n".join(f"  {e['ts']}  {e['type']:<12} {fmt(int(e['amount_minor']), e['currency']):>12}  decision {e['decision_id']}"
                      for e in ledger)
    text = (f"Receipt for order {order_id}\n\nItem: {o.get('item_name')}\nPaid: {fmt(int(o['amount_minor']), o['currency'])}\n"
            f"Status: {o['status']}\n\nLedger:\n{lines}\n\nEvery entry above carries a signed AgentSafe decision id.")
    ids = []
    for who in ("buyer", "seller"):
        email = o.get(f"{who}_email")
        if email:
            ids.append(notify.send(channel="email", to=email, subject=f"Clasp receipt · order {order_id[-6:]}", text=text,
                                   order_id=order_id, correlation_id=o.get("correlation_id", "")))
    return {"message_ids": ids}


def mark_disputed(order_id: str, output: dict | None = None) -> dict:
    output = output or {}
    o = get_stores().update_order(order_id, {"status": "DISPUTED", "dispute": {"reason": output.get("reason", ""),
                                                                               "opened_at": clock.now(),
                                                                               "reopened": bool(output.get("after_rejection"))}},
                                  expected_status=["DISPATCHED", "DELIVERED", "ESCALATED", "DISPUTED"])
    if output.get("after_rejection"):
        notify.send(channel="whatsapp", to=o["buyer_phone"], order_id=order_id, correlation_id=o.get("correlation_id", ""),
                    text="The supervisor did not approve the full refund. Let's settle this within policy: I can still offer "
                         "the options from the compensation ladder.")
    else:
        notify.send(channel="rcs", to=o["seller_phone"], order_id=order_id, correlation_id=o.get("correlation_id", ""),
                    text=f"Order {order_id[-6:]}: the buyer reported a problem. Clasp has called a Truce; the payout stays "
                         "held while evidence is collected. No action needed from you yet.")
    return {"status": "DISPUTED"}


def mark_escalated(order_id: str, output: dict | None = None) -> dict:
    output = output or {}
    o = get_stores().update_order(order_id, {"status": "ESCALATED", "escalation": {
        "decision_id": output.get("decision_id"), "amount_minor": output.get("amount_minor"),
        "currency": output.get("currency", "USD"), "kind": output.get("kind", "refund"), "opened_at": clock.now()}},
        expected_status=["DISPUTED"])
    notify.send(channel="whatsapp", to=o["buyer_phone"], order_id=order_id, correlation_id=o.get("correlation_id", ""),
                text=f"A refund of {fmt(int(output.get('amount_minor', 0)), output.get('currency', 'USD'))} is above what I can "
                     "approve on my own. A supervisor has been asked to review it; I'll let you know here.")
    return {"status": "ESCALATED"}


def send_approval_email(order_id: str, output: dict | None = None) -> dict:
    from ..agentsafe import adapter as agentsafe

    o = _order(order_id)
    esc = o.get("escalation") or {}
    amount, currency = int(esc.get("amount_minor") or 0), esc.get("currency", "USD")
    links = agentsafe.approval_links(order_id=order_id, decision_id=esc.get("decision_id", "-"), amount_minor=amount,
                                     currency=currency, approver=settings.supervisor_email)
    base = settings.api_base_url
    approve_url, reject_url = f"{base}/approvals?token={links['approve']}", f"{base}/approvals?token={links['reject']}"
    verdicts = [c for c in get_stores().challenges_for_order(order_id) if c.get("kind") == "dispute" and c.get("verdict")]
    verdict = verdicts[-1]["verdict"] if verdicts else {}
    text = (f"Approval needed · order {order_id}\n\n"
            f"Refund requested: {fmt(amount, currency)} (order value {fmt(int(o['amount_minor']), currency)}; "
            f"automatic cap {fmt(5000, currency)} / 30%).\nAgentSafe decision: {esc.get('decision_id')} (escalate)\n"
            f"Buyer's reason: {(o.get('dispute') or {}).get('reason', '-')}\n"
            f"Evidence verdict: {json.dumps(verdict) if verdict else 'none recorded'}\n\n"
            f"Approve: {approve_url}\n\nReject: {reject_url}\n\nLinks are signed and expire in 48 hours.")
    html = (f"<p><b>Approval needed · order {order_id}</b></p><p>Refund requested: <b>{fmt(amount, currency)}</b> "
            f"(order value {fmt(int(o['amount_minor']), currency)}).<br>AgentSafe decision {esc.get('decision_id')} → escalate.</p>"
            f"<p>Buyer's reason: {(o.get('dispute') or {}).get('reason', '-')}</p><p>Evidence verdict: <code>{json.dumps(verdict)}</code></p>"
            f"<p><a href='{approve_url}' style='padding:10px 16px;background:#0a7d3e;color:#fff;border-radius:8px;text-decoration:none'>Approve refund</a>"
            f"&nbsp;&nbsp;<a href='{reject_url}' style='padding:10px 16px;background:#b00020;color:#fff;border-radius:8px;text-decoration:none'>Reject</a></p>"
            f"<p style='color:#666;font-size:12px'>Signed links, 48h expiry. Approving mints a fresh AgentSafe authorization; it is still single-use and amount-bound.</p>")
    mid = notify.send(channel="email", to=settings.supervisor_email, subject=f"[Clasp] Approve {fmt(amount, currency)} refund · order {order_id[-6:]}",
                      text=text, html=html, order_id=order_id, correlation_id=o.get("correlation_id", ""))
    return {"message_id": mid, "approve_url": approve_url, "reject_url": reject_url}


def send_evidence_pack(order_id: str, output: dict | None = None) -> dict:
    o = _order(order_id)
    stores = get_stores()
    ledger = stores.ledger_entries(order_id)
    decisions = stores.decisions_for_order(order_id)
    challenges = stores.challenges_for_order(order_id)
    pack = {"order": {k: o.get(k) for k in ("order_id", "item_name", "amount_minor", "currency", "status", "resolution", "dispute")},
            "ledger": ledger, "decisions": [{k: d.get(k) for k in ("decision_id", "action", "amount_minor", "decision", "reason", "ts")} for d in decisions],
            "evidence": [{k: c.get(k) for k in ("challenge_id", "kind", "status", "verdict", "created_at")} for c in challenges]}
    body = json.dumps(pack, indent=2, default=str)
    outcome = o.get("resolution") or o.get("status")
    text = (f"Truce resolved · order {order_id}\n\nOutcome: {outcome}\n"
            f"Money moved: " + ", ".join(f"{e['type']} {fmt(int(e['amount_minor']), e['currency'])}" for e in ledger if e["type"] in {"REFUND", "CREDIT"}) +
            "\n\nThe attached evidence pack lists every AgentSafe decision (allowed and denied), the ledger, and the "
            "challenge-response evidence verdicts.")
    ids = []
    for who in ("buyer", "seller"):
        email = o.get(f"{who}_email")
        if email:
            ids.append(notify.send(channel="email", to=email, subject=f"Clasp evidence pack · order {order_id[-6:]} · {outcome}",
                                   text=text, order_id=order_id, correlation_id=o.get("correlation_id", ""),
                                   attachments=[{"filename": f"evidence-{order_id}.json", "content_type": "application/json",
                                                 "data": body.encode()}]))
    return {"message_ids": ids}


STEPS = {
    "register_token": None,  # handled in handler (needs the task token)
    "notify_seller_funds_held": notify_seller_funds_held,
    "mark_dispatched": mark_dispatched,
    "send_delivery_code": send_delivery_code,
    "send_receipts": send_receipts,
    "mark_disputed": mark_disputed,
    "mark_escalated": mark_escalated,
    "send_approval_email": send_approval_email,
    "send_evidence_pack": send_evidence_pack,
}
