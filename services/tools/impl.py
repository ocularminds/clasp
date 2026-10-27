"""Tool implementations the agent can call (through AgentCore Gateway when deployed,
directly in simulation). Conventions:

* `actor_sub` and `correlation_id` are injected by the runtime for every call (a Strands
  hook overwrites whatever the model wrote). The model cannot pick who it is acting as.
* Evidence flags are never taken from the model. `propose_refund` looks up the verified
  challenge in the store itself.
* Nothing here writes the ledger. Money goes propose -> AgentSafe -> executor.
"""
from __future__ import annotations

from typing import Any

from ..agentsafe.policy import Policy
from ..common import clock
from ..common.config import settings
from ..common.ids import new_code, new_id
from ..common.log import Log
from ..common.media import get_media
from ..common.money import fmt, percent_of
from ..common.stores import get_stores
from ..workflows import tasks
from ..workflows.client import get_driver
from . import evidence as evidence_mod
from .knowledge import explain_policy
from .money import propose

log = Log("tools")

_policy: Policy | None = None


def policy() -> Policy:
    global _policy
    if _policy is None:
        _policy = Policy.load()
    return _policy


class ToolError(Exception):
    pass


def _actor_orders(stores, actor_sub: str, allow_seller: bool, seller_only: bool = False) -> list[dict]:
    rows: dict[str, dict] = {}
    if not seller_only:
        rows.update({o["order_id"]: o for o in stores.orders_for_buyer(actor_sub)})
    if allow_seller:
        for o in stores.orders_for_seller(actor_sub):
            rows.setdefault(o["order_id"], o)
    return sorted(rows.values(), key=lambda o: str(o.get("created_at", "")), reverse=True)


def _order_for(order_ref: str | None, actor_sub: str, allow_seller: bool = False, *,
               single_status: str | None = None, seller_only: bool = False) -> dict:
    """Resolve an order the actor may act on. Accepts the full order ID or the short code shown in alerts
    (the last 6 characters). With no reference and `single_status`, picks the actor's only order in that status.
    Short codes are matched only against the actor's own orders, so they never widen what anyone can reach."""
    stores = get_stores()
    ref = (order_ref or "").strip()
    if ref:
        o = stores.get_order(ref)
        if o:
            if o.get("buyer_sub") != actor_sub and not (allow_seller and o.get("seller_sub") == actor_sub):
                raise ToolError("this order does not belong to you")
            return o
        code = ref.lower().removeprefix("ord_").strip(" .…#")
        if len(code) < 4:
            raise ToolError(f"order {ref} not found")
        matches = [o for o in _actor_orders(stores, actor_sub, allow_seller, seller_only) if o["order_id"].lower().endswith(code)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ToolError(f"'{ref}' matches {len(matches)} of your orders; please give the full order ID")
        raise ToolError(f"order {ref} not found")
    if single_status:
        candidates = [o for o in _actor_orders(stores, actor_sub, allow_seller, seller_only) if o.get("status") == single_status]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise ToolError(f"you have no order in status {single_status}")
        raise ToolError(f"you have {len(candidates)} orders in status {single_status}; give the order code from your alert")
    raise ToolError("order_id is required")


def _summary(o: dict) -> dict:
    return {
        "order_id": o["order_id"], "item": o.get("item_name"), "status": o.get("status"),
        "status_words": tasks.STATUS_WORDS.get(o.get("status", ""), str(o.get("status", "")).lower()),
        "amount": fmt(int(o["amount_minor"]), o["currency"]), "amount_minor": int(o["amount_minor"]), "currency": o["currency"],
        "discount": fmt(int(o.get("discount_minor", 0)), o["currency"]),
        "held": fmt(int(o.get("held_minor", 0)), o["currency"]), "refunded": fmt(int(o.get("refunded_minor", 0)), o["currency"]),
        "resolution": o.get("resolution"), "checkout_url": o.get("checkout_url"), "short_code": o["order_id"][-6:],
    }


# --------------------------------------------------------------------------- catalogue
def list_items(**_: Any) -> dict:
    items = get_stores().list_items()
    return {"items": [{"item_id": i["item_id"], "name": i["name"], "price": fmt(int(i["price_minor"]), i["currency"]),
                       "price_minor": int(i["price_minor"]), "currency": i["currency"], "stock": int(i.get("stock", 0)),
                       "description": i.get("description", "")} for i in items]}


def get_item(item_id: str, **_: Any) -> dict:
    i = get_stores().get_item(item_id)
    if not i:
        # tolerate a name-ish query
        for cand in get_stores().list_items():
            if item_id.lower() in cand["name"].lower():
                i = cand
                break
    if not i:
        raise ToolError(f"no item '{item_id}'")
    return {**i, "price": fmt(int(i["price_minor"]), i["currency"]), "in_stock": int(i.get("stock", 0)) > 0,
            "max_discount_percent": policy().actions.get("discount", {}).get("max_percent", 0)}


# --------------------------------------------------------------------------- buy
def start_order(item_id: str, actor_sub: str, qty: int = 1, correlation_id: str = "", **_: Any) -> dict:
    stores = get_stores()
    item = stores.get_item(item_id)
    if not item:
        raise ToolError(f"no item '{item_id}'")
    buyer = stores.party_by_sub(actor_sub)
    if not buyer:
        raise ToolError("I can't identify you as a registered buyer")
    seller = stores.get_party(item["seller_id"])
    if not seller:
        raise ToolError("seller not found")
    if not stores.reserve_stock(item_id, qty):
        raise ToolError(f"'{item['name']}' is out of stock")
    amount = int(item["price_minor"]) * int(qty)
    order = {
        "order_id": new_id("ord"), "buyer_sub": actor_sub, "buyer_phone": buyer.get("phone"), "buyer_email": buyer.get("email"),
        "buyer_name": buyer.get("name"), "seller_id": seller["party_id"], "seller_sub": seller.get("sub"),
        "seller_phone": seller.get("phone"), "seller_email": seller.get("email"), "seller_name": seller.get("name"),
        "item_id": item_id, "item_name": item["name"], "qty": int(qty), "list_amount_minor": amount, "discount_minor": 0,
        "amount_minor": amount, "currency": item["currency"], "status": "PAYMENT_PENDING", "held_minor": 0,
        "released_minor": 0, "refunded_minor": 0, "credited_minor": 0, "tokens": {},
        "created_at": str(clock.now()).zfill(12), "version": 0, "correlation_id": correlation_id or new_id("corr"),
    }
    stores.put_order(order)
    arn = get_driver().start_order(order)
    if arn:
        stores.update_order(order["order_id"], {"execution_arn": arn})
    log.info("order.started", order_id=order["order_id"], item_id=item_id, amount_minor=amount, correlation_id=order["correlation_id"])
    return {**_summary(stores.get_order(order["order_id"])), "message": "Order created; nothing is charged until the buyer pays through the hosted link."}


def propose_discount(order_id: str, percent: float, actor_sub: str, correlation_id: str = "", **_: Any) -> dict:
    o = _order_for(order_id, actor_sub)
    amount = percent_of(int(o["list_amount_minor"]), float(percent))
    if amount <= 0:
        raise ToolError("discount must be positive")
    return propose("discount", order_id=order_id, amount_minor=amount, currency=o["currency"], actor_sub=actor_sub,
                   metadata={"percent": float(percent)}, correlation_id=correlation_id or o.get("correlation_id", ""))


def create_checkout(order_id: str, actor_sub: str, correlation_id: str = "", **_: Any) -> dict:
    from ..psp_mock.checkout import checkout_url

    o = _order_for(order_id, actor_sub)
    if o.get("status") != "PAYMENT_PENDING":
        raise ToolError(f"order is {o.get('status')}; checkout is only for unpaid orders")
    checkout_id = o.get("checkout_id") or new_id("chk")
    url = checkout_url(checkout_id)
    get_stores().update_order(order_id, {"checkout_id": checkout_id, "checkout_url": url})
    return {"order_id": order_id, "checkout_url": url, "amount": fmt(int(o["amount_minor"]), o["currency"]),
            "note": "Share this link. Card details go to the payment provider, never into the chat. "
                    "Payment is confirmed by the provider's webhook; screenshots are not accepted."}


def get_order(order_id: str, actor_sub: str, **_: Any) -> dict:
    o = _order_for(order_id, actor_sub, allow_seller=True)
    ledger = get_stores().ledger_entries(order_id)
    return {**_summary(o), "ledger": [{"type": e["type"], "amount": fmt(int(e["amount_minor"]), e["currency"]),
                                        "decision_id": e["decision_id"]} for e in ledger]}


def my_orders(actor_sub: str, **_: Any) -> dict:
    """Recent orders of the person we are talking to, as buyer or as seller (sellers see what awaits dispatch)."""
    stores = get_stores()
    seller_ids = {o["order_id"] for o in stores.orders_for_seller(actor_sub)}
    rows = [{**_summary(o), "your_role": "seller" if o["order_id"] in seller_ids and o.get("buyer_sub") != actor_sub else "buyer"}
            for o in _actor_orders(stores, actor_sub, allow_seller=True)]
    party = stores.party_by_sub(actor_sub) or {}
    return {"orders": rows[:5], "email_on_file": bool(party.get("email"))}


def save_email(email: str, actor_sub: str, **_: Any) -> dict:
    """The person asks for receipts and evidence packs by email. Stored on their own party record only."""
    import re

    email = (email or "").strip()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email):
        raise ToolError("that doesn't look like an email address")
    stores = get_stores()
    party = stores.party_by_sub(actor_sub)
    if not party:
        raise ToolError("I can't identify you")
    stores.put_party({**party, "email": email})
    log.info("party.email_saved", party_id=party.get("party_id"))
    return {"saved": True, "message": "Saved. Receipts and evidence packs for your orders will go to that address."}


# --------------------------------------------------------------------------- dispatch → deliver
# Dispatch is recorded by the courier's signed pickup scan (services/courier_mock), never by a chat message.


def confirm_delivery(order_id: str, code: str, actor_sub: str, correlation_id: str = "", **_: Any) -> dict:
    o = _order_for(order_id, actor_sub)
    if o.get("status") not in {"DISPATCHED", "DELIVERED"}:
        raise ToolError(f"order is {o.get('status')}; a delivery code only applies after dispatch")
    stores = get_stores()
    open_codes = [c for c in stores.challenges_for_order(order_id) if c.get("kind") == "delivery" and c.get("status") == "open"]
    match = next((c for c in open_codes if tasks.code_matches(code, c["code_hash"]) and int(c["expires_at"]) > clock.now()), None)
    if not match:
        attempts = int(o.get("delivery_code_attempts", 0)) + 1
        stores.update_order(order_id, {"delivery_code_attempts": attempts})
        log.warn("delivery_code.mismatch", order_id=order_id, attempts=attempts, correlation_id=o.get("correlation_id") or None)
        hint = " If the code never arrived, I can send a new one here." if attempts >= 2 else ""
        return {"verified": False, "attempts": attempts,
                "message": f"That code doesn't match. Please check the latest delivery code from Clasp and try again.{hint}"}
    stores.update_challenge(order_id, match["challenge_id"], {"status": "verified", "verified_at": clock.now()})
    available = int(o.get("held_minor", 0))
    return {"verified": True, **propose("release", order_id=order_id, amount_minor=available, currency=o["currency"],
                                        actor_sub=actor_sub, evidence={"delivery_code_verified": True, "challenge_id": match["challenge_id"]},
                                        metadata={"challenge_id": match["challenge_id"]},
                                        correlation_id=correlation_id or o.get("correlation_id", ""))}


MAX_DELIVERY_CODES = 3


def resend_delivery_code(order_id: str, actor_sub: str, **_: Any) -> dict:
    """Buyer didn't get the delivery code: a fresh one, here in WhatsApp. Older codes stop working."""
    o = _order_for(order_id, actor_sub)
    if o.get("status") != "DISPATCHED":
        raise ToolError(f"order is {tasks.STATUS_WORDS.get(o.get('status', ''), o.get('status'))}; a delivery code is only for an order on its way")
    sent = [c for c in get_stores().challenges_for_order(o["order_id"]) if c.get("kind") == "delivery"]
    if len(sent) >= MAX_DELIVERY_CODES:
        raise ToolError("the delivery code has already been sent the maximum number of times; tell the buyer to check their messages")
    out = tasks.issue_delivery_code(o["order_id"], prefer="whatsapp")
    return {"sent": bool(out["channel"]), "channel": out["channel"],
            "message": "A new delivery code has been sent. Earlier codes no longer work."}


# --------------------------------------------------------------------------- Truce
def report_problem(order_id: str, description: str, actor_sub: str, correlation_id: str = "", **_: Any) -> dict:
    o = _order_for(order_id, actor_sub)
    if o.get("status") in {"DISPATCHED", "DELIVERED"}:
        token = (o.get("tokens") or {}).get("delivery")
        if not token:
            raise ToolError("the order is not waiting for delivery")
        get_driver().task_success(token, {"outcome": "dispute", "order_id": order_id, "reason": description[:500]})
    elif o.get("status") != "DISPUTED":
        raise ToolError(f"order is {o.get('status')}; a problem can be reported after dispatch and before release")
    challenge = issue_challenge(order_id, actor_sub, correlation_id=correlation_id)
    return {"order_id": order_id, "status": "DISPUTED", "truce": True, **challenge,
            "message": "Truce called: the payout stays held. Give the buyer the instruction exactly as written."}


def issue_challenge(order_id: str, actor_sub: str, correlation_id: str = "", **_: Any) -> dict:
    o = _order_for(order_id, actor_sub)
    if o.get("status") not in {"DISPUTED", "ESCALATED"}:
        raise ToolError(f"order is {o.get('status')}; challenges are issued during a Truce")
    stores = get_stores()
    for c in stores.challenges_for_order(order_id):
        if c.get("kind") == "dispute" and c.get("status") == "open":
            stores.update_challenge(order_id, c["challenge_id"], {"status": "superseded"})
    code = new_code(6)
    ch = {"order_id": order_id, "challenge_id": new_id("chl"), "kind": "dispute", "code_hash": tasks.code_hash(code), "code": code,
          "created_at": clock.now(), "expires_at": clock.now() + tasks.CHALLENGE_CODE_TTL, "status": "open",
          "correlation_id": correlation_id or o.get("correlation_id", "")}
    stores.put_challenge(ch)
    log.info("challenge.issued", order_id=order_id, challenge_id=ch["challenge_id"], correlation_id=ch["correlation_id"] or None)
    return {"challenge_id": ch["challenge_id"], "code": code, "expires_in_seconds": tasks.CHALLENGE_CODE_TTL,
            "instruction": f"Write {code} on a piece of paper, put it next to the item, and send me one photo (or a video under "
                           f"30 seconds) showing both, within {tasks.CHALLENGE_CODE_TTL // 60} minutes."}


def verify_evidence(order_id: str, photo_key: str | None = None, actor_sub: str = "", challenge_id: str | None = None,
                    correlation_id: str = "", media_key: str | None = None, **_: Any) -> dict:
    """`media_key` is a photo or a short video; `photo_key` is the older name for the same thing."""
    photo_key = media_key or photo_key
    if not photo_key:
        raise ToolError("media_key is required")
    o = _order_for(order_id, actor_sub)
    stores = get_stores()
    challenges = [c for c in stores.challenges_for_order(order_id) if c.get("kind") == "dispute"]
    ch = next((c for c in challenges if c["challenge_id"] == challenge_id), None) if challenge_id else (challenges[-1] if challenges else None)
    if not ch or ch.get("status") not in {"open", "failed"}:
        raise ToolError("no open challenge; issue one first")
    if int(ch["expires_at"]) <= clock.now():
        stores.update_challenge(order_id, ch["challenge_id"], {"status": "expired"})
        raise ToolError("the challenge code expired; call issue_challenge for a fresh one and give the buyer the new instruction")
    media = get_media()
    live = media.get(photo_key)
    dispatch = media.get(o["dispatch_photo_key"]) if o.get("dispatch_photo_key") and media.exists(o["dispatch_photo_key"]) else None
    verdict = evidence_mod.analyze(ch.get("code", ""), dispatch, live, correlation_id=correlation_id or o.get("correlation_id", ""))
    verified = bool(verdict["code_visible"] and verdict["code_matches"] and verdict["confidence"] >= 0.6)
    stores.update_challenge(order_id, ch["challenge_id"], {"status": "verified" if verified else "failed", "verdict": verdict,
                                                            "live_photo_key": photo_key, "verified_at": clock.now() if verified else None})
    return {"challenge_id": ch["challenge_id"], "challenge_verified": verified, **verdict,
            "note": "The model only reports. AgentSafe decides whether any money moves." if verified else
                    "Evidence not verified: no refund can be authorized. Offer to issue a fresh code."}


def _verified_challenge(order_id: str) -> dict | None:
    for c in reversed(get_stores().challenges_for_order(order_id)):
        if c.get("kind") == "dispute" and c.get("status") == "verified":
            return c
    return None


def get_truce_options(order_id: str, actor_sub: str, **_: Any) -> dict:
    o = _order_for(order_id, actor_sub)
    opts = policy().truce_options(o)
    return {"order_id": order_id, "options": opts, "evidence_verified": _verified_challenge(order_id) is not None,
            "auto_cap": fmt(int(policy().actions["refund"].get("auto_cap_minor", 0)), o["currency"]),
            "note": "Offer at most three of these as reply buttons. Anything else is decided by AgentSafe and may need the seller's approval."}


def _resolution(action: str, kind: str, order_id: str, amount_minor: int, actor_sub: str, correlation_id: str) -> dict:
    o = _order_for(order_id, actor_sub)
    order_id = o["order_id"]
    ch = _verified_challenge(order_id)
    # Evidence comes from our own store, never from the model: the verdict feeds AgentSafe's drift checks.
    evidence = ({"challenge_verified": True, "challenge_id": ch["challenge_id"], "verdict": ch.get("verdict") or {}}
                if ch else {"challenge_verified": False})
    result = propose(action, order_id=order_id, amount_minor=amount_minor, currency=o["currency"], actor_sub=actor_sub,
                     evidence=evidence, metadata={"kind": kind, "challenge_id": ch["challenge_id"] if ch else None},
                     correlation_id=correlation_id or o.get("correlation_id", ""))
    if result["decision"] == "escalate" and o.get("status") == "DISPUTED":
        token = (o.get("tokens") or {}).get("dispute")
        if token:
            get_driver().task_success(token, {"outcome": "escalate", "order_id": order_id, "decision_id": result["decision_id"],
                                              "amount_minor": amount_minor, "currency": o["currency"], "kind": kind,
                                              "action": action, "reason": result.get("reason", "")})
            result["escalated"] = True
            result["note"] = ("Tell the buyer this in your own words: the request has gone to the seller for approval, nothing "
                              "has been paid yet, and you'll message them here with the answer.")
    return result


def propose_refund(order_id: str, amount_minor: int, actor_sub: str, correlation_id: str = "", **_: Any) -> dict:
    return _resolution("refund", "partial_refund", order_id, int(amount_minor), actor_sub, correlation_id)


def propose_credit(order_id: str, amount_minor: int, actor_sub: str, correlation_id: str = "", **_: Any) -> dict:
    return _resolution("credit", "store_credit", order_id, int(amount_minor), actor_sub, correlation_id)


def propose_replacement(order_id: str, actor_sub: str, correlation_id: str = "", **_: Any) -> dict:
    return _resolution("replacement", "replacement", order_id, 0, actor_sub, correlation_id)


def offer_options(order_id: str, option_kinds: list[str], actor_sub: str, **_: Any) -> dict:
    """Record the options the agent is putting to the buyer; returns WhatsApp reply buttons (max 3)."""
    o = _order_for(order_id, actor_sub)
    by_kind = {opt["kind"]: opt for opt in policy().truce_options(o)}
    chosen = [by_kind[k] for k in option_kinds if k in by_kind][:3]
    if not chosen:
        raise ToolError(f"no valid options among {option_kinds}; valid: {list(by_kind)}")
    get_stores().update_order(order_id, {"pending_offers": chosen})
    return {"buttons": [{"id": f"truce:{order_id}:{c['kind']}", "title": c["label"][:20]} for c in chosen],
            "options": chosen}


TOOLS = {
    "list_items": list_items, "get_item": get_item, "start_order": start_order, "propose_discount": propose_discount,
    "create_checkout": create_checkout, "get_order": get_order, "my_orders": my_orders, "save_email": save_email,
    "confirm_delivery": confirm_delivery, "resend_delivery_code": resend_delivery_code, "report_problem": report_problem,
    "issue_challenge": issue_challenge, "verify_evidence": verify_evidence, "get_truce_options": get_truce_options,
    "propose_refund": propose_refund, "propose_credit": propose_credit, "propose_replacement": propose_replacement,
    "offer_options": offer_options, "explain_policy": explain_policy,
}


def call(name: str, args: dict[str, Any]) -> dict:
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(**args)
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
