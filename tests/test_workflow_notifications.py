"""Keep demo SMS useful in one segment and follow an active WhatsApp dispatch chat."""
from __future__ import annotations

import pytest

from services.common import clock
from services.notify.router import MemoryNotifier
from services.workflows import tasks


def test_hold_and_delivery_sms_fit_one_segment(make_order, monkeypatch):
    outbox = MemoryNotifier()
    monkeypatch.setattr(tasks.notify, "get_notifier", lambda: outbox)
    order = make_order(status="HELD", amount=10800)
    tasks.notify_seller_funds_held(order["order_id"])
    held = outbox.last("rcs").fallback_text
    assert held.isascii() and len(held) <= 160
    assert "$108.00" in held and "simulation" in held and order["order_id"][-6:] in held
    assert "WhatsApp" in held and "photo" in held

    tasks.send_delivery_code(order["order_id"])
    delivery = outbox.last("sms").text
    assert delivery.isascii() and len(delivery) <= 160
    assert "releases the seller's payout" in delivery


@pytest.mark.parametrize("photo_key,age,expected_channel", [
    ("inbound/15550000002/photo", 60, "whatsapp"),
    ("inbound/15550000002/photo", 24 * 3600, "rcs"),
    ("inbound/rcs/message/photo", 60, "rcs"),
    ("dispatch/legacy.jpg", 60, "rcs"),
])
def test_truce_uses_recent_dispatch_conversation(make_order, stores, monkeypatch, photo_key, age, expected_channel):
    outbox = MemoryNotifier()
    monkeypatch.setattr(tasks.notify, "get_notifier", lambda: outbox)
    order = make_order(status="DISPATCHED", dispatch_photo_key=photo_key, dispatched_at=clock.now() - age)
    tasks.mark_disputed(order["order_id"], {"reason": "Battery cover damaged"})
    message = outbox.last()
    assert message.channel == expected_channel
    assert message.to == order["seller_phone"] and "Truce" in message.text
    final = stores.get_order(order["order_id"])
    assert final["status"] == "DISPUTED"
    assert final["held_minor"] == order["held_minor"] and final["refunded_minor"] == 0
