"""PSP webhook receiver. Verifies the HMAC, then proposes a HOLD as `system:psp`.
The hold is still an AgentSafe decision + executor write; the webhook has no authority of its own."""
from __future__ import annotations

import json

from ..common.config import settings
from ..common.ids import correlation_id as new_corr
from ..common.log import Log
from ..common.stores import get_stores
from ..tools.money import propose
from .checkout import SIG_HEADER, verify

log = Log("psp.webhook")


def handle_webhook(body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
    sig = {k.lower(): v for k, v in (headers or {}).items()}.get(SIG_HEADER)
    if not verify(settings.psp_webhook_secret, body, sig):
        log.warn("webhook.bad_signature")
        return 401, {"error": "invalid signature"}
    try:
        event = json.loads(body)
    except ValueError:
        return 400, {"error": "bad json"}
    if event.get("type") != "payment.succeeded":
        return 200, {"ignored": event.get("type")}
    data = event.get("data") or {}
    stores = get_stores()
    order = stores.order_by_checkout(str(data.get("checkout_id", ""))) or stores.get_order(str(data.get("order_id", "")))
    if not order:
        return 404, {"error": "unknown checkout"}
    corr = order.get("correlation_id") or new_corr()
    if order.get("psp_event_id") == event["id"]:
        return 200, {"duplicate": True, "order_id": order["order_id"]}
    if order.get("status") != "PAYMENT_PENDING":
        return 200, {"ignored": f"order is {order.get('status')}", "order_id": order["order_id"]}
    log.info("webhook.verified", order_id=order["order_id"], psp_event_id=event["id"], correlation_id=corr)
    result = propose(
        "hold", order_id=order["order_id"], amount_minor=int(data.get("amount_minor", -1)),
        currency=str(data.get("currency", order["currency"])), actor_sub="system:psp",
        evidence={"psp_verified": True, "psp_event_id": event["id"]},
        metadata={"psp_event_id": event["id"], "last4": data.get("last4")}, correlation_id=corr,
    )
    return 200, {"order_id": order["order_id"], **result}
