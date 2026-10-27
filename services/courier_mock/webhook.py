"""Courier webhook receiver. Verifies the HMAC, then completes the order's dispatch wait with the pickup photo.
Dispatch moves no money; the workflow then sends the buyer's delivery code."""
from __future__ import annotations

import json

from ..common.config import settings
from ..common.log import Log
from ..common.stores import get_stores
from ..psp_mock.checkout import verify
from ..workflows.client import get_driver
from .courier import SIG_HEADER

log = Log("courier.webhook")


def handle_webhook(body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
    sig = {k.lower(): v for k, v in (headers or {}).items()}.get(SIG_HEADER)
    if not verify(settings.courier_webhook_secret, body, sig):
        log.warn("courier.bad_signature")
        return 401, {"error": "invalid signature"}
    try:
        event = json.loads(body)
    except ValueError:
        return 400, {"error": "bad json"}
    if event.get("type") != "shipment.picked_up":
        return 200, {"ignored": event.get("type")}
    data = event.get("data") or {}
    stores = get_stores()
    order = stores.get_order(str(data.get("order_id", "")))
    if not order:
        return 404, {"error": "unknown order"}
    shipment = order.get("shipment") or {}
    if shipment.get("shipment_id") != data.get("shipment_id"):
        return 409, {"error": "shipment does not match the order"}
    if shipment.get("pickup_event_id") == event.get("id"):
        return 200, {"duplicate": True, "order_id": order["order_id"]}
    token = (order.get("tokens") or {}).get("dispatch")
    if shipment.get("status") != "booked" or not token:
        return 409, {"error": "the order is not waiting for a pickup"}
    stores.update_order(order["order_id"], {"shipment.status": "scanned", "shipment.pickup_event_id": event.get("id")})
    log.info("courier.pickup", order_id=order["order_id"], shipment_id=shipment.get("shipment_id"),
             has_photo=bool(data.get("photo_key")), correlation_id=order.get("correlation_id") or None)
    get_driver().task_success(token, {"outcome": "dispatched", "order_id": order["order_id"],
                                      "dispatch_photo_key": data.get("photo_key"), "shipment_id": shipment.get("shipment_id")})
    return 200, {"order_id": order["order_id"], "status": "DISPATCHED"}
