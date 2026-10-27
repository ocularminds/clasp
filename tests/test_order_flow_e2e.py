"""M2: a whole order, offline. Local workflow runner, moto DynamoDB + KMS, memory notifier.
Buy → signed webhook → HOLD → courier booked (seller told, not asked) → signed pickup scan → SMS code →
(release | Truce → settle | escalate to the seller, then a supervisor)."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from demo.seed import seed
from services.agentsafe.signer import KmsVerifier
from services.approvals import handler as approvals
from services.common.media import LocalMedia, set_media
from services.courier_mock import courier
from services.courier_mock.webhook import handle_webhook as courier_webhook
from services.executor.executor import Executor, set_executor
from services.notify.router import MemoryNotifier, set_notifier
from services.psp_mock import checkout
from services.psp_mock.handler import pay
from services.psp_mock.webhook import handle_webhook
from services.tools import impl as tools
from services.workflows.client import set_driver
from services.workflows.local_runner import LocalDriver

DAMAGE = ('{"code_visible": true, "code_matches": true, "same_item_as_dispatch": true, "damage_observed": true, '
          '"confidence": 0.93, "notes": "torn strap visible"}')


@pytest.fixture
def world(stores, kms, kms_key, agentsafe, tmp_path, monkeypatch):
    monkeypatch.setenv("PSP_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("COURIER_WEBHOOK_SECRET", "test-courier-secret")
    monkeypatch.setenv("API_BASE_URL", "https://api.example.test")
    driver = LocalDriver()
    set_driver(driver)
    ex = Executor(KmsVerifier(kms_key, kms), stores, driver)
    set_executor(ex)
    outbox = MemoryNotifier()
    set_notifier(outbox)
    media = LocalMedia(tmp_path / "media")
    set_media(media)
    media.put("dispatch/demo.jpg", b"\xff\xd8\xff fake jpeg bytes")
    media.put("live/demo.jpg", b"\xff\xd8\xff another fake jpeg")
    subs = seed(stores)
    yield {"stores": stores, "outbox": outbox, "buyer": subs["buyer_demo"], "seller": subs["seller_demo"], "driver": driver}
    set_driver(None)
    set_executor(None)
    set_notifier(None)
    set_media(None)


def buy_and_hold(w) -> dict:
    buyer = w["buyer"]
    order = tools.start_order("item_bag", actor_sub=buyer, correlation_id="corr-e2e")
    oid = order["order_id"]
    assert order["status"] == "PAYMENT_PENDING"
    assert w["stores"].get_item("item_bag")["stock"] == 4

    disc = tools.propose_discount(oid, 10, actor_sub=buyer)
    assert disc["decision"] == "allow" and disc["executed"]
    too_much = tools.propose_discount(oid, 5, actor_sub=buyer)  # 10% + 5% > cap
    assert too_much["decision"] == "deny" and too_much["message"] == "The most I can take off is 10%."

    co = tools.create_checkout(oid, actor_sub=buyer)
    assert co["checkout_url"].startswith("https://api.example.test/pay/")
    checkout_id = co["checkout_url"].rsplit("/", 1)[-1]

    # a forged webhook (bad signature) changes nothing
    status, body = handle_webhook(b'{"type":"payment.succeeded"}', {"x-clasp-signature": "t=1,v1=00"})
    assert status == 401 and w["stores"].get_order(oid)["status"] == "PAYMENT_PENDING"

    status, body = pay(checkout_id)
    assert status == 200 and body["decision"] == "allow" and body["executed"], body
    o = w["stores"].get_order(oid)
    assert o["status"] == "HELD" and o["held_minor"] == 10800 and o["amount_minor"] == 10800
    card = w["outbox"].last("rcs")
    assert card and card.rich_card["title"] == "Payment secured" and "no reply needed" in card.text
    assert "suggestions" not in card.rich_card  # the seller is told, not asked
    assert o["tokens"]["dispatch"] and o["shipment"]["status"] == "booked"  # waiting for the courier, not the seller
    receipt = w["outbox"].last("email")
    assert receipt.to == "buyer@example.com" and receipt.subject.startswith("Clasp receipt: $108.00")

    # replaying the same webhook is a no-op
    status, body = pay(checkout_id)
    assert status == 200 and body.get("ignored") or body.get("duplicate")
    assert len(w["stores"].ledger_entries(oid)) == 2  # DISCOUNT + HOLD
    return w["stores"].get_order(oid)


def dispatch(w, oid: str) -> str:
    """The courier scans the pickup: signed webhook, parcel photo, then the delivery code by SMS."""
    order = w["stores"].get_order(oid)
    forged = json.dumps(courier.pickup_event(order, None)).encode()
    assert courier_webhook(forged, {courier.SIG_HEADER: "t=1,v1=00"})[0] == 401
    assert w["stores"].get_order(oid)["status"] == order["status"]  # a forged scan changes nothing
    status, body = courier.scan_pickup(order, b"\xff\xd8\xff parcel at pickup")
    assert status == 200 and body["status"] == "DISPATCHED", body
    o = w["stores"].get_order(oid)
    assert o["status"] == "DISPATCHED" and o["dispatch_photo_key"].startswith(f"courier/{oid}/")
    assert courier.scan_pickup(o, b"\xff\xd8\xff again")[0] == 409  # one pickup per shipment
    note = w["outbox"].last("whatsapp")
    assert note.to == "+15550000001" and "picked up by the courier" in note.text and note.media_url
    sms = w["outbox"].last("sms")
    assert sms and sms.to == "+15550000001" and len(sms.text) <= 160
    return re.search(r" is ([A-Z0-9]{6})\.", sms.text).group(1)


def test_happy_path_release_on_delivery_code(world):
    w = world
    o = buy_and_hold(w)
    code = dispatch(w, o["order_id"])
    bad = tools.confirm_delivery(o["order_id"], "WRONG1", actor_sub=w["buyer"])
    assert bad["verified"] is False
    ok = tools.confirm_delivery(o["order_id"], code.lower(), actor_sub=w["buyer"])
    assert ok["verified"] and ok["decision"] == "allow" and ok["executed"]
    final = w["stores"].get_order(o["order_id"])
    assert final["status"] == "RELEASED" and final["released_minor"] == 10800
    assert [e["type"] for e in w["stores"].ledger_entries(o["order_id"])] == ["DISCOUNT", "HOLD", "RELEASE"]
    receipts = [m for m in w["outbox"].for_channel("email") if "delivery confirmed" in m.subject or "payout released" in m.subject]
    assert {m.to for m in receipts} == {"buyer@example.com", "seller@example.com"}
    assert "$108.00 has been released to you" in w["outbox"].last("rcs").text
    assert final["tokens"]["delivery"] == ""  # consumed


def test_resend_delivery_code_goes_to_whatsapp_and_retires_the_old_code(world):
    w = world
    o = buy_and_hold(w)
    old = dispatch(w, o["order_id"])
    r = tools.resend_delivery_code(o["order_id"][-6:], actor_sub=w["buyer"])
    assert r["sent"] and r["channel"] == "whatsapp"
    new = re.search(r" is ([A-Z0-9]{6})\.", w["outbox"].last("whatsapp").text).group(1)
    assert tools.confirm_delivery(o["order_id"], old, actor_sub=w["buyer"])["verified"] is False
    assert tools.confirm_delivery(o["order_id"], new, actor_sub=w["buyer"])["executed"]
    notices = w["stores"].get_order(o["order_id"])["notices"]
    assert all(new not in n["memo"] and old not in n["memo"] for n in notices)  # codes never reach the agent's context


def test_truce_injection_denied_then_partial_refund_and_seller_gets_the_rest(world, monkeypatch):
    w = world
    o = buy_and_hold(w)
    dispatch(w, o["order_id"])
    oid = o["order_id"]

    truce = tools.report_problem(oid, "The strap arrived torn.", actor_sub=w["buyer"])
    assert truce["status"] == "DISPUTED" and len(truce["code"]) == 6 and "15 minutes" in truce["instruction"]
    assert w["stores"].get_order(oid)["status"] == "DISPUTED"
    seller_note = w["outbox"].for_channel("rcs")[-1]
    assert "Truce" in seller_note.text and "Nothing to do yet" in seller_note.text

    # The injection: the model proposes it; AgentSafe denies it. No evidence yet either.
    inj = tools.propose_refund(oid, 100_000, actor_sub=w["buyer"])
    assert inj["decision"] == "deny" and "executed" not in inj
    assert inj["reason"].startswith("evidence_required") and "live photo or video" in inj["message"]

    monkeypatch.setenv("EVIDENCE_STUB_JSON", DAMAGE)
    ev = tools.verify_evidence(oid, actor_sub=w["buyer"], media_key="live/demo.jpg")
    assert ev["challenge_verified"] is True

    inj2 = tools.propose_refund(oid, 100_000, actor_sub=w["buyer"])
    assert inj2["decision"] == "deny" and inj2["reason"].startswith("amount_exceeds_held_funds")
    assert inj2["message"] == "That's more than the $108.00 held for this order."

    opts = tools.get_truce_options(oid, actor_sub=w["buyer"])
    assert [x["kind"] for x in opts["options"]] == ["partial_refund", "replacement", "store_credit"]
    partial = next(x for x in opts["options"] if x["kind"] == "partial_refund")
    assert partial["amount_minor"] == 2160  # 20% of 108.00

    buttons = tools.offer_options(oid, ["partial_refund", "replacement", "store_credit"], actor_sub=w["buyer"])
    assert len(buttons["buttons"]) == 3 and all(len(b["title"]) <= 20 for b in buttons["buttons"])

    res = tools.propose_refund(oid, partial["amount_minor"], actor_sub=w["buyer"])
    assert res["decision"] == "allow" and res["executed"] and res["status"] == "RESOLVED_PARTIAL_REFUND"
    final = w["stores"].get_order(oid)
    # Both sides kept whole: the buyer's 20% back, the rest of the hold to the seller, each its own decision.
    assert final["refunded_minor"] == 2160 and final["released_minor"] == 8640 and final["status"] == "RESOLVED_PARTIAL_REFUND"
    assert [e["type"] for e in w["stores"].ledger_entries(oid)] == ["DISCOUNT", "HOLD", "REFUND", "RELEASE"]
    assert "$86.40 has been released to you" in w["outbox"].last("rcs").text
    packs = [m for m in w["outbox"].for_channel("email") if "Truce settled" in (m.subject or "")]
    assert len(packs) == 2 and packs[0].subject.endswith("partial refund ($21.60)")
    assert packs[0].attachments[0]["filename"] == f"clasp-evidence-{oid[-6:]}.json" and "RESOLVED_" not in packs[0].text
    decisions = w["stores"].decisions_for_order(oid)
    assert [(d["action"], d["decision"]) for d in decisions][-2:] == [("refund", "allow"), ("settle", "allow")]
    assert [d["decision"] for d in decisions] == ["allow", "deny", "allow", "deny", "deny", "allow", "allow"]


def _links(w, to: str) -> tuple[str, str]:
    mail = [m for m in w["outbox"].for_channel("email") if m.to == to][-1]
    tok = lambda k: parse_qs(urlparse(re.search(rf"{k}: (\S+)", mail.text).group(1)).query)["token"][0]  # noqa: E731
    return tok("Approve"), tok("Reject")


def _escalate_full_refund(w) -> str:
    o = buy_and_hold(w)
    dispatch(w, o["order_id"])
    oid = o["order_id"]
    tools.report_problem(oid, "Wrong item entirely.", actor_sub=w["buyer"])
    tools.verify_evidence(oid, actor_sub=w["buyer"], media_key="live/demo.jpg")  # default stub verdict: verified, damage
    full = tools.propose_refund(oid, 10800, actor_sub=w["buyer"])
    assert full["decision"] == "escalate" and full.get("escalated") is True and "seller has been asked" in full["message"]
    o2 = w["stores"].get_order(oid)
    assert o2["status"] == "ESCALATED" and o2["tokens"]["approval"] and o2["escalation"]["stage"] == "seller"
    assert not [m for m in w["outbox"].for_channel("email") if m.to == "supervisor@example.com"]  # seller first
    return oid


def test_seller_approves_an_above_cap_refund_by_signed_link(world):
    w = world
    oid = _escalate_full_refund(w)
    approve_token, reject_token = _links(w, "seller@example.com")
    mail = [m for m in w["outbox"].for_channel("email") if m.to == "seller@example.com"][-1]
    assert "above the automatic limit ($50.00 or 30% of the order)" in mail.text and "24 hours" in mail.text

    # tampering with the link (different amount in the claims) buys nothing
    from services.agentsafe import jws
    h, p, s = approve_token.split(".")
    claims = json.loads(jws.b64u_decode(p)); claims["amount_minor"] = 1
    forged = f"{h}.{jws.b64u(json.dumps(claims, separators=(',', ':'), sort_keys=True).encode())}.{s}"
    assert approvals.handle(forged)["statusCode"] == 400 and w["stores"].get_order(oid)["status"] == "ESCALATED"

    # opening the link (GET) changes nothing: mail scanners can't approve
    page = approvals.confirm(approve_token)
    assert page["statusCode"] == 200 and "method='post'" in page["body"] and "Nothing has changed yet" in page["body"]
    assert [e["type"] for e in w["stores"].ledger_entries(oid)] == ["DISCOUNT", "HOLD"]

    page = approvals.handle(approve_token)
    assert page["statusCode"] == 200 and "has been approved" in page["body"]
    final = w["stores"].get_order(oid)
    assert final["status"] == "RESOLVED_REFUND" and final["refunded_minor"] == 10800 and final["tokens"]["approval"] == ""
    assert "the seller approved your refund of $108.00" in [m for m in w["outbox"].for_channel("whatsapp")][-1].text
    assert "no longer pending" in approvals.handle(approve_token)["body"]
    assert "no longer pending" in approvals.handle(reject_token)["body"]
    assert [e["type"] for e in w["stores"].ledger_entries(oid)] == ["DISCOUNT", "HOLD", "REFUND"]
    assert w["stores"].decisions_for_order(oid)[-1]["reason"].startswith("seller_approved")


def test_seller_rejects_then_supervisor_approves(world):
    w = world
    oid = _escalate_full_refund(w)
    seller_approve, seller_reject = _links(w, "seller@example.com")
    page = approvals.handle(seller_reject)
    assert page["statusCode"] == 200 and "A Clasp supervisor will now decide" in page["body"]
    o = w["stores"].get_order(oid)
    assert o["status"] == "ESCALATED" and o["escalation"]["stage"] == "supervisor" and o["escalation"]["seller_outcome"] == "rejected"
    assert "a Clasp supervisor is reviewing it now" in w["outbox"].last("whatsapp").text
    # the seller's approve link is dead once the decision has moved on
    assert "moved on" in approvals.handle(seller_approve)["body"]
    sup_approve, _ = _links(w, "supervisor@example.com")
    assert "The seller rejected it." in [m for m in w["outbox"].for_channel("email") if m.to == "supervisor@example.com"][-1].text
    assert approvals.handle(sup_approve)["statusCode"] == 200
    final = w["stores"].get_order(oid)
    assert final["status"] == "RESOLVED_REFUND" and w["stores"].decisions_for_order(oid)[-1]["reason"].startswith("supervisor_approved")


def test_seller_silence_moves_the_decision_to_a_supervisor(world):
    w = world
    oid = _escalate_full_refund(w)
    w["driver"].timeout(oid, "approval")
    o = w["stores"].get_order(oid)
    assert o["escalation"]["stage"] == "supervisor" and o["escalation"]["seller_outcome"] == "timeout"
    assert "hasn't answered" in w["outbox"].last("whatsapp").text
    assert "didn't answer in time" in [m for m in w["outbox"].for_channel("email") if m.to == "supervisor@example.com"][-1].text


def test_supervisor_rejection_returns_to_truce_with_buttons(world):
    w = world
    oid = _escalate_full_refund(w)
    approvals.handle(_links(w, "seller@example.com")[1])
    page = approvals.handle(_links(w, "supervisor@example.com")[1])
    assert page["statusCode"] == 200 and "standard Truce options" in page["body"]
    back = w["stores"].get_order(oid)
    assert back["status"] == "DISPUTED" and back["tokens"]["dispute"] and back["dispute"]["reopened"]
    assert back["dispute"]["reason"] == "Wrong item entirely."  # the buyer's words survive the rejection
    msg = w["outbox"].last("whatsapp")
    assert "couldn't approve the $108.00 refund" in msg.text and len(msg.buttons) == 3
    # in-policy option still works after the rejection
    res = tools.propose_credit(oid, 3240, actor_sub=w["buyer"])  # 30% store credit
    assert res["decision"] == "allow" and res["status"] == "RESOLVED_CREDIT"
    assert w["stores"].get_order(oid)["released_minor"] == 10800 - 3240  # the rest goes to the seller


def test_inconclusive_evidence_is_drift_even_in_policy(world, monkeypatch):
    w = world
    o = buy_and_hold(w)
    dispatch(w, o["order_id"])
    oid = o["order_id"]
    tools.report_problem(oid, "Battery cover cracked.", actor_sub=w["buyer"])
    monkeypatch.setenv("EVIDENCE_STUB_JSON", DAMAGE.replace('"damage_observed": true', '"damage_observed": false'))
    tools.verify_evidence(oid, actor_sub=w["buyer"], media_key="live/demo.jpg")
    r = tools.propose_refund(oid, 2160, actor_sub=w["buyer"])  # the 20% ladder amount, under every cap
    assert r["decision"] == "escalate" and "inconclusive_evidence (no damage visible)" in r["reason"]
    mail = [m for m in w["outbox"].for_channel("email") if m.to == "seller@example.com"][-1]
    assert "does not clearly show damage" in mail.text and "$86.40 is paid to you" in mail.text


def test_replacement_ships_again_and_pays_the_seller_on_delivery(world, monkeypatch):
    w = world
    o = buy_and_hold(w)
    dispatch(w, o["order_id"])
    oid = o["order_id"]
    tools.report_problem(oid, "Wrong colour.", actor_sub=w["buyer"])
    monkeypatch.setenv("EVIDENCE_STUB_JSON", DAMAGE)
    tools.verify_evidence(oid, actor_sub=w["buyer"], media_key="live/demo.jpg")
    r = tools.propose_replacement(oid, actor_sub=w["buyer"])
    assert r["decision"] == "allow" and r["status"] == "RESOLVED_REPLACEMENT"
    o2 = w["stores"].get_order(oid)
    assert o2["shipment"]["replacement"] is True and o2["shipment"]["status"] == "booked"
    code = dispatch(w, oid)
    assert "replacement" in w["outbox"].last("whatsapp").text
    assert tools.confirm_delivery(oid, code, actor_sub=w["buyer"])["executed"]
    final = w["stores"].get_order(oid)
    assert final["status"] == "RELEASED" and final["released_minor"] == 10800


def test_webhook_signature_helpers():
    body = b'{"a":1}'
    sig = checkout.sign("s", body, ts=1000)
    assert checkout.verify("s", body, sig, now=1100)
    assert not checkout.verify("s", body, sig, now=5000)  # stale
    assert not checkout.verify("other", body, sig, now=1100)
    assert not checkout.verify("s", b'{"a":2}', sig, now=1100)


def test_seller_sees_orders_by_short_code_but_cannot_act_in_chat(world):
    w = world
    seller = w["seller"]
    o1 = buy_and_hold(w)
    code1 = o1["order_id"][-6:]
    mine = tools.my_orders(actor_sub=seller)
    assert len(mine["orders"]) == 1 and mine["orders"][0]["your_role"] == "seller" and mine["orders"][0]["short_code"] == code1
    assert mine["orders"][0]["status_words"] == "paid and held"
    assert tools.get_order(code1, actor_sub=seller)["order_id"] == o1["order_id"]
    assert tools.get_order(f"…{code1}", actor_sub=w["buyer"])["order_id"] == o1["order_id"]
    with pytest.raises(tools.ToolError, match="not found"):
        tools.get_order(code1, actor_sub="sub-buyer_demo_2")  # not this buyer's order
    assert "record_dispatch" not in tools.TOOLS and "notify_party" not in tools.TOOLS


def test_save_email_reaches_receipts(world):
    w = world
    w["stores"].put_party({**w["stores"].party_by_sub(w["buyer"]), "email": ""})
    assert tools.my_orders(actor_sub=w["buyer"])["email_on_file"] is False
    with pytest.raises(tools.ToolError):
        tools.save_email("not-an-email", actor_sub=w["buyer"])
    tools.save_email("ada@example.org", actor_sub=w["buyer"])
    assert tools.my_orders(actor_sub=w["buyer"])["email_on_file"] is True
    from services.workflows import tasks
    assert tasks.email_for({"buyer_sub": w["buyer"], "buyer_email": "old@example.com"}, "buyer") == "ada@example.org"
