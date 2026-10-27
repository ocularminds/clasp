"""API Gateway (HTTP API v2) routes for the mock courier:
  GET  /courier/{order_id}/{shipment_id}   driver pickup page (phone camera)
  POST /courier/{order_id}/{shipment_id}   scan: {"photo_b64": ...} → signed pickup webhook
  POST /webhooks/courier                   webhook receiver (HMAC verified)"""
from __future__ import annotations

import base64
import binascii
import json

from ..common.stores import get_stores
from . import courier
from .webhook import handle_webhook


def _resp(status: int, body: str | dict, content_type: str = "application/json") -> dict:
    return {"statusCode": status, "headers": {"content-type": content_type},
            "body": body if isinstance(body, str) else json.dumps(body)}


def _body_bytes(event: dict) -> bytes:
    raw = event.get("body") or ""
    return base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode("utf-8")


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "")
    if path.startswith("/webhooks/courier") and method == "POST":
        status, body = handle_webhook(_body_bytes(event), event.get("headers") or {})
        return _resp(status, body)
    if path.startswith("/courier/"):
        params = event.get("pathParameters") or {}
        parts = path.strip("/").split("/")
        order_id = params.get("order_id") or (parts[1] if len(parts) > 2 else "")
        shipment_id = params.get("shipment_id") or (parts[2] if len(parts) > 2 else "")
        order = get_stores().get_order(order_id)
        html_type = "text/html; charset=utf-8"
        if method == "GET":
            return _resp(200, courier.render_page(order, shipment_id), html_type)
        if method == "POST":
            if not order or (order.get("shipment") or {}).get("shipment_id") != shipment_id:
                return _resp(404, courier.render_page(None, shipment_id), html_type)
            try:
                photo = base64.b64decode(json.loads(_body_bytes(event) or b"{}").get("photo_b64") or "", validate=True)
            except (ValueError, binascii.Error):
                return _resp(400, courier.render_page(order, shipment_id, error="That photo could not be read. Try again."), html_type)
            if len(photo) > courier.MAX_PHOTO_BYTES or (photo and photo[:3] != b"\xff\xd8\xff"):
                return _resp(400, courier.render_page(order, shipment_id, error="Please send a JPEG photo under 4 MB."), html_type)
            status, body = courier.scan_pickup(order, photo or None)
            ok = status == 200
            return _resp(200 if ok else status, courier.render_page(get_stores().get_order(order_id), shipment_id, done=ok,
                                                                    error=None if ok else body.get("error")), html_type)
    return _resp(404, {"error": f"no route for {method} {path}"})
