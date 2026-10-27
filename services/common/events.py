"""Order lifecycle events on an EventBridge bus (EVENTS_MODE=eventbridge), printed (console), recorded
in memory (memory) or off. Source `clasp.order`. Events are observability and integration hooks: they are
never on the money path, so a failed publish is logged and swallowed. Details never carry delivery or
challenge codes, task tokens, signed links or phone numbers."""
from __future__ import annotations

import json
from typing import Any

from . import clock
from .config import settings
from .ids import new_id
from .log import Log

log = Log("events")

SOURCE = "clasp.order"
EVENT_TYPES = ("OrderCreated", "OrderHeld", "CourierBooked", "OrderDispatched", "DeliveryCodeSent", "OrderReleased",
               "TruceOpened", "OrderEscalated", "ApprovalRequested", "OrderResolved", "OrderSettled")
DELIVERY_EVENT_TYPES = ("CourierBooked", "OrderDispatched", "DeliveryCodeSent", "OrderReleased")
_FORBIDDEN_KEYS = {"code", "token", "tokens", "approve_url", "reject_url", "buyer_phone", "seller_phone", "phone"}


class MemoryEvents:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def types(self) -> list[str]:
        return [e["detail_type"] for e in self.events]


_recorder: MemoryEvents | None = None


def recorder() -> MemoryEvents:
    global _recorder
    if _recorder is None:
        _recorder = MemoryEvents()
    return _recorder


def reset_recorder() -> None:
    global _recorder
    _recorder = None


def _detail(detail_type: str, order: dict, extra: dict[str, Any]) -> dict:
    detail = {
        "event_id": new_id("evt"), "detail_type": detail_type, "order_id": order.get("order_id"), "status": order.get("status"),
        "item_id": order.get("item_id"), "item_name": order.get("item_name"), "amount_minor": order.get("amount_minor"),
        "held_minor": order.get("held_minor"), "currency": order.get("currency"), "correlation_id": order.get("correlation_id"),
        "stage": settings.stage, "at": clock.now(),
    }
    for k, v in extra.items():
        if k in _FORBIDDEN_KEYS:
            raise ValueError(f"event detail must not carry '{k}'")
        detail[k] = v
    return detail


def emit(detail_type: str, order: dict, **extra: Any) -> str | None:
    """Publish one lifecycle event. Returns the provider event id (or our own id in local modes)."""
    if detail_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {detail_type}")
    mode = settings.events_mode
    if mode == "off":
        return None
    detail = _detail(detail_type, order, extra)
    if mode == "memory":
        recorder().events.append(detail)
        return detail["event_id"]
    if mode == "eventbridge":
        try:
            from .aws import client

            r = client("events").put_events(Entries=[{
                "Source": SOURCE, "DetailType": detail_type, "EventBusName": settings.event_bus_name,
                "Detail": json.dumps(detail, default=str),
            }])
            entry = (r.get("Entries") or [{}])[0]
            if r.get("FailedEntryCount"):
                log.error("events.put_failed", detail_type=detail_type, order_id=detail["order_id"],
                          code=entry.get("ErrorCode"), message=entry.get("ErrorMessage"))
                return None
            log.info("events.put", detail_type=detail_type, order_id=detail["order_id"], event_id=entry.get("EventId"),
                     correlation_id=detail.get("correlation_id") or None)
            return entry.get("EventId") or detail["event_id"]
        except Exception as e:  # noqa: BLE001 — never let observability break the order
            log.error("events.put_error", detail_type=detail_type, order_id=detail["order_id"], error=str(e))
            return None
    oid = str(detail.get("order_id") or "")[-6:]
    print(f"  ⚡ event {detail_type:<16} order {oid}  status={detail.get('status')}")
    return detail["event_id"]
