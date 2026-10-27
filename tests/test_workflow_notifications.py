"""Seller notices are one-way and fit one SMS segment; every notice survives a failing channel."""
from __future__ import annotations

from services.notify.router import MemoryNotifier
from services.workflows import tasks

GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
           "abcdefghijklmnopqrstuvwxyzäöñüà")


def one_segment(text: str) -> bool:
    return all(c in GSM7 for c in text) and len(text) <= 160


def test_seller_and_buyer_sms_fit_one_segment(make_order, monkeypatch):
    outbox = MemoryNotifier()
    monkeypatch.setattr(tasks.notify, "get_notifier", lambda: outbox)
    order = make_order(status="HELD", amount=10800)
    tasks.book_courier(order["order_id"])
    held = outbox.last("rcs").fallback_text
    assert one_segment(held) and "$108.00" in held and "test mode" in held and order["order_id"][-6:] in held
    assert "No reply needed" in held and "photo" not in held  # the seller is told, not asked

    tasks.send_delivery_code(order["order_id"])
    delivery = outbox.last("sms").text
    assert one_segment(delivery) and "only once you have the item" in delivery

    shipped = make_order(status="DISPATCHED", amount=10800)
    tasks.mark_disputed(shipped["order_id"], {"reason": "Battery cover damaged"})
    truce = outbox.last("rcs")
    assert truce.to == order["seller_phone"] and one_segment(truce.fallback_text) and "Nothing to do yet" in truce.fallback_text


def test_a_failing_channel_falls_through_to_the_next(make_order, stores, monkeypatch):
    class FlakyNotifier(MemoryNotifier):
        def send(self, msg):
            if msg.channel in {"rcs", "sms"}:
                raise RuntimeError("sandbox spend limit reached")
            return super().send(msg)

    outbox = FlakyNotifier()
    monkeypatch.setattr(tasks.notify, "get_notifier", lambda: outbox)
    order = make_order(status="DISPATCHED", amount=10800)
    tasks.mark_disputed(order["order_id"], {"reason": "Battery cover damaged"})
    assert outbox.last().channel == "email" and outbox.last().to == "seller@example.com"  # seller notice by email instead
    out = tasks.send_delivery_code(order["order_id"])
    assert out["channel"] == "whatsapp" and "delivery code" in outbox.last("whatsapp").text  # buyer code by WhatsApp
    notices = stores.get_order(order["order_id"])["notices"]
    assert [n["channel"] for n in notices] == ["email", "whatsapp"]
    assert stores.get_order(order["order_id"])["status"] == "DISPUTED"  # the order kept moving


def test_a_replacement_request_is_never_described_as_zero_dollars():
    assert tasks.asked_for({"kind": "replacement", "amount_minor": 0}, "USD") == "replacement"
    assert tasks.asked_for({"kind": "refund", "amount_minor": 10800, "currency": "USD"}, "USD") == "$108.00 refund"


def test_allowed_resolutions_say_what_happened():
    from services.agentsafe.policy import Policy, describe

    p = Policy.load()
    req = {"action": "refund", "amount_minor": 2160, "currency": "USD"}
    assert describe(p, "allow", "in_policy", req, None) == \
        "Your $21.60 refund is approved and recorded (test mode: no real money moves)."
    assert describe(p, "allow", "seller_approved: the seller approved decision dec_1", req, None).startswith(
        "The seller approved it. Your $21.60 refund")
    held = describe(p, "escalate", "decionis_escalate: review", req, None)
    assert "seller has been asked to approve" in held and "Decionis" not in held and "AgentSafe" not in held
    # The code may have come by SMS or in the chat, so the reply doesn't name a channel.
    no_code = describe(p, "deny", "evidence_required: release requires a delivery code", {"action": "release"}, None)
    assert no_code == "I need the one-time delivery code we sent you first."
