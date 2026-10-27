"""A model-free stand-in for the agent: enough intent matching to prove the ingress → tools →
channel plumbing offline (AGENT_MODE=scripted). The real agent is services/agent/agent.py."""
from __future__ import annotations

import re

from ..common.stores import get_stores
from ..tools import impl as tools
from ..workflows.tasks import STATUS_WORDS


def _active_order(sub: str) -> dict | None:
    for o in get_stores().orders_for_buyer(sub):
        if not str(o.get("status", "")).startswith("RESOLVED") and o.get("status") != "RELEASED":
            return o
    return None


def handle_turn(payload: dict) -> dict:
    text = (payload.get("text") or "").strip()
    sub = payload["actor_sub"]
    corr = payload.get("correlation_id", "")
    low = text.lower()
    called: list[str] = []
    button = payload.get("button") or {}
    order = _active_order(sub)

    def run(name, **args):
        called.append(name)
        return tools.call(name, {**args, "actor_sub": sub, "correlation_id": corr})

    if payload.get("actor_role") == "seller":
        mine = run("my_orders")["orders"]
        if mine:
            return {"text": "Your recent orders: " + ", ".join(f"{o['short_code']} ({o['item']}, {o['status_words']})" for o in mine)
                    + ". Nothing is needed from you: a courier collects each parcel, and you'll get an email if a decision needs you.",
                    "tools_called": called}
        return {"text": "You have no orders right now.", "tools_called": called}

    if button.get("id", "").startswith("truce:") and order:
        kind = button["id"].split(":")[-1]
        opts = {o["kind"]: o for o in run("get_truce_options", order_id=order["order_id"])["options"]}
        opt = opts.get(kind)
        if not opt:
            return {"text": "That option is no longer available.", "tools_called": called}
        r = run({"refund": "propose_refund", "credit": "propose_credit", "replacement": "propose_replacement"}[opt["action"]],
                order_id=order["order_id"], **({"amount_minor": opt["amount_minor"]} if opt["action"] != "replacement" else {}))
        if r.get("executed"):
            return {"text": f"Done: {opt['label']}" + (f" ({r['amount']})" if opt["amount_minor"] else "") + ". The evidence pack is on "
                            "its way to both of you by email.", "tools_called": called}
        if r.get("decision") == "escalate":
            return {"text": f"{r.get('message')} Nothing has been paid yet; I'll message you here with the answer.", "tools_called": called}
        return {"text": f"I couldn't do that: {r.get('message') or r.get('error')}", "tools_called": called}

    if any(w in low for w in ("available", "buy", "bag", "price", "discount")) and not order:
        item = run("get_item", item_id="item_bag")
        o = run("start_order", item_id=item["item_id"])
        parts = [f"Yes, the {item['name']} is in stock at {item['price']}."]
        if "discount" in low:
            d = run("propose_discount", order_id=o["order_id"], percent=10)
            parts.append(f"I can take 10% off. {d.get('message')}")
        co = run("create_checkout", order_id=o["order_id"])
        parts.append(f"Pay securely here: {co['checkout_url']} (card details never go in this chat).")
        return {"text": " ".join(parts), "order_id": o["order_id"], "tools_called": called}

    if order and re.search(r"\b(never (got|arrived|came)|didn'?t (get|receive)|no code)\b", low) and order.get("status") == "DISPATCHED":
        r = run("resend_delivery_code", order_id=order["order_id"])
        return {"text": r.get("message") or r.get("error", ""), "tools_called": called}

    if order and re.fullmatch(r"[A-Za-z0-9]{6}", text) and order.get("status") == "DISPATCHED":
        r = run("confirm_delivery", order_id=order["order_id"], code=text)
        return {"text": "Thanks! Delivery confirmed, and the seller's payout has been released." if r.get("executed")
                else r.get("message") or "That code didn't work.", "tools_called": called}

    m = re.search(r"refund (?:me )?\$?([\d,]+)", low)
    if order and m and order.get("status") in {"DISPUTED", "ESCALATED"}:
        amount = int(m.group(1).replace(",", "")) * 100
        r = run("propose_refund", order_id=order["order_id"], amount_minor=amount)
        if r.get("decision") == "deny":
            opts = run("get_truce_options", order_id=order["order_id"])
            b = run("offer_options", order_id=order["order_id"], option_kinds=[o["kind"] for o in opts["options"]])
            return {"text": f"I asked, and policy said no: {r['message']} Here's what I can do:", "buttons": b["buttons"],
                    "tools_called": called}
        if r.get("decision") == "escalate":
            return {"text": f"{r['message']} Nothing has been paid yet; I'll message you here with the answer.", "tools_called": called}
        return {"text": f"Refund of {r['amount']} issued.", "tools_called": called}

    if order and any(w in low for w in ("damaged", "torn", "broken", "wrong", "problem")) and order.get("status") == "DISPATCHED":
        r = run("report_problem", order_id=order["order_id"], description=text)
        return {"text": f"I'm sorry. When something goes wrong, Clasp calls a Truce: the seller's payout stays on hold. "
                        f"{r.get('instruction', '')}", "tools_called": called}

    if order and payload.get("media_keys") and order.get("status") == "DISPUTED":
        r = run("verify_evidence", order_id=order["order_id"], media_key=payload["media_keys"][0])
        return {"text": "Thanks, the code checks out. How would you like to settle this?" if r.get("challenge_verified")
                else f"I couldn't verify that: {r.get('notes') or r.get('error')}", "tools_called": called}

    if order:
        words = STATUS_WORDS.get(order["status"], order["status"].lower())
        return {"text": f"Your order {order['order_id'][-6:]} is {words}. How can I help?", "tools_called": called}
    return {"text": "Hi, I'm Clasp. Ask me about an item to get started.", "tools_called": called}
