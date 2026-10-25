"""API Gateway (HTTP API v2) routes for the mock PSP:
  GET  /pay/{checkout_id}      hosted checkout page
  POST /pay/{checkout_id}      "Pay" → emits a signed webhook to POST /webhooks/psp
  POST /webhooks/psp           webhook receiver (HMAC verified)"""
from __future__ import annotations

import base64
import json

from ..common.config import settings
from ..common.stores import get_stores
from . import checkout
from .webhook import handle_webhook


def _resp(status: int, body: str | dict, content_type: str = "application/json") -> dict:
    return {"statusCode": status, "headers": {"content-type": content_type},
            "body": body if isinstance(body, str) else json.dumps(body)}


def _body_bytes(event: dict) -> bytes:
    raw = event.get("body") or ""
    return base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode("utf-8")


def deliver_webhook(payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    sig = checkout.sign(settings.psp_webhook_secret, body)
    if (settings.api_base_url.startswith("http") and settings.psp == "mock"
            and __import__("os").environ.get("PSP_WEBHOOK_DELIVERY", "inprocess") == "http"):
        import requests

        r = requests.post(f"{settings.api_base_url}/webhooks/psp", data=body,
                          headers={"content-type": "application/json", checkout.SIG_HEADER: sig}, timeout=15)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"raw": r.text[:500]}
    return handle_webhook(body, {checkout.SIG_HEADER: sig})


def pay(checkout_id: str) -> tuple[int, dict]:
    order = get_stores().order_by_checkout(checkout_id)
    if not order:
        return 404, {"error": "unknown checkout"}
    return deliver_webhook(checkout.payment_event(order))


def lambda_handler(event, context):
    route = event.get("routeKey") or f"{event.get('requestContext', {}).get('http', {}).get('method')} {event.get('rawPath')}"
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "")
    params = event.get("pathParameters") or {}
    stores = get_stores()

    if path.startswith("/webhooks/psp") and method == "POST":
        status, body = handle_webhook(_body_bytes(event), event.get("headers") or {})
        return _resp(status, body)
    if path.startswith("/pay/"):
        checkout_id = params.get("checkout_id") or path.rsplit("/", 1)[-1]
        order = stores.order_by_checkout(checkout_id)
        if method == "GET":
            return _resp(200, checkout.render_page(order), "text/html; charset=utf-8")
        if method == "POST":
            status, body = pay(checkout_id)
            order = stores.order_by_checkout(checkout_id)
            ok = status == 200 and body.get("executed")
            return _resp(200 if ok else 402, checkout.render_page(order, paid=bool(ok), error=None if ok else json.dumps(body)),
                         "text/html; charset=utf-8")
    return _resp(404, {"error": f"no route for {route}"})
