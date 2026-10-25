"""Mock payment provider: hosted checkout page + HMAC-signed webhook events.
Card data never touches the chat: the agent only ever shares a link to this page."""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import time

from ..common.config import settings
from ..common.ids import new_id
from ..common.money import fmt

SIG_HEADER = "x-clasp-signature"


def sign(secret: str, body: bytes, ts: int | None = None) -> str:
    ts = int(ts if ts is not None else time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify(secret: str, body: bytes, header: str | None, tolerance: int = 300, now: int | None = None) -> bool:
    if not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts.get("t", "0"))
    except ValueError:
        return False
    now = int(now if now is not None else time.time())
    if abs(now - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))


def payment_event(order: dict) -> dict:
    return {
        "id": new_id("evt"), "type": "payment.succeeded", "created": int(time.time()),
        "data": {"checkout_id": order["checkout_id"], "order_id": order["order_id"],
                 "amount_minor": int(order["amount_minor"]), "currency": order["currency"],
                 "payment_method": "card", "last4": "4242", "provider": "mock"},
    }


def checkout_url(checkout_id: str) -> str:
    return f"{settings.api_base_url}/pay/{checkout_id}"


def render_page(order: dict | None, paid: bool = False, error: str | None = None) -> str:
    if order is None:
        return _page("Checkout not found", "<p>This payment link is invalid or has expired.</p>")
    amount = fmt(int(order["amount_minor"]), order["currency"])
    item = html.escape(order.get("item_name", "item"))
    if paid or order.get("status") not in {"PAYMENT_PENDING", "DRAFT"}:
        return _page("Payment received", f"<p class='ok'>✔ {amount} received for <b>{item}</b>.</p>"
                                         "<p>The seller is paid only after you confirm delivery. Back to the chat.</p>")
    err = f"<p class='err'>{html.escape(error)}</p>" if error else ""
    body = f"""
      <p>Pay <b>{amount}</b> for <b>{item}</b></p>
      <p class='muted'>Order {html.escape(order['order_id'])} · funds are held until delivery is confirmed</p>
      {err}
      <form method='post'>
        <label>Card number <input value='4242 4242 4242 4242' readonly></label>
        <label>Expiry <input value='12/29' readonly></label>
        <label>CVC <input value='123' readonly></label>
        <button type='submit'>Pay {amount}</button>
      </form>
      <p class='muted'>Mock provider. Pressing Pay sends an HMAC-signed webhook to Clasp. No real card is charged.</p>"""
    return _page("Clasp secure checkout", body)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{html.escape(title)}</title><style>
body{{font-family:-apple-system,system-ui,sans-serif;max-width:420px;margin:40px auto;padding:0 16px;color:#1b1b1f;background:#f6f6f8}}
h1{{font-size:20px}} label{{display:block;margin:10px 0;font-size:14px}} input{{display:block;width:100%;padding:10px;border:1px solid #ccc;border-radius:8px;background:#fff}}
button{{width:100%;padding:14px;border:0;border-radius:10px;background:#111;color:#fff;font-size:16px;margin-top:12px}}
.muted{{color:#666;font-size:13px}} .ok{{color:#0a7d3e;font-size:18px}} .err{{color:#b00020}}
</style></head><body><h1>🔒 {html.escape(title)}</h1>{body}</body></html>"""
