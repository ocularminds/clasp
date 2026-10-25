"""M2: a whole order, offline. Local workflow runner, moto DynamoDB + KMS, memory notifier.
Buy → signed webhook → HOLD → RCS card → dispatch → SMS code → (release | Truce | escalate)."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest

from demo.seed import seed
from services.agentsafe.signer import KmsVerifier
from services.approvals import handler as approvals
from services.common.media import LocalMedia, set_media
from services.executor.executor import Executor, set_executor
from services.notify.router import MemoryNotifier, set_notifier
from services.psp_mock import checkout
from services.psp_mock.handler import pay
from services.psp_mock.webhook import handle_webhook
from services.tools import impl as tools
from services.workflows.client import set_driver
from services.workflows.local_runner import LocalDriver
from tests.conftest import REGION


@pytest.fixture
def world(stores, kms, kms_key, agentsafe, tmp_path, monkeypatch):
    monkeypatch.setenv("PSP_WEBHOOK_SECRET", "test-secret")
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
    assert too_much["decision"] == "deny"

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
    assert card and card.rich_card["title"] == "Funds secured by provider"
    assert o["tokens"]["dispatch"]  # waiting for the seller

    # replaying the same webhook is a no-op
    status, body = pay(checkout_id)
    assert status == 200 and body.get("ignored") or body.get("duplicate")
    assert len(w["stores"].ledger_entries(oid)) == 2  # DISCOUNT + HOLD
    return w["stores"].get_order(oid)


def dispatch(w, oid: str) -> str:
    with pytest.raises(tools.ToolError):
        tools.record_dispatch(oid, actor_sub=w["buyer"], photo_key="dispatch/demo.jpg")  # buyer can't
    r = tools.record_dispatch(oid, actor_sub=w["seller"], photo_key="dispatch/demo.jpg")
    assert r["status"] == "DISPATCHED"
    sms = w["outbox"].last("sms")
    assert sms and sms.to == "+15550000001"
    code = re.search(r": ([A-Z0-9]{6})\.", sms.text).group(1)
    assert w["stores"].get_order(oid)["status"] == "DISPATCHED"
    return code


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
    receipts = w["outbox"].for_channel("email")
    assert {m.to for m in receipts} == {"buyer@example.com", "seller@example.com"}
    assert final["tokens"]["delivery"] == ""  # consumed


def test_truce_injection_denied_then_partial_refund(world, monkeypatch):
    w = world
    o = buy_and_hold(w)
    dispatch(w, o["order_id"])
    oid = o["order_id"]

    truce = tools.report_problem(oid, "The strap arrived torn.", actor_sub=w["buyer"])
    assert truce["status"] == "DISPUTED" and len(truce["code"]) == 6
    assert w["stores"].get_order(oid)["status"] == "DISPUTED"
    seller_note = w["outbox"].for_channel("rcs")[-1]
    assert "Truce" in seller_note.text

    # The injection: the model proposes it; AgentSafe denies it. No evidence yet either.
    inj = tools.propose_refund(oid, 100_000, actor_sub=w["buyer"])
    assert inj["decision"] == "deny" and "executed" not in inj
    assert inj["reason"].startswith("evidence_required")  # no verified challenge yet

    monkeypatch.setenv("EVIDENCE_STUB_JSON", '{"code_visible": true, "code_matches": true, "same_item_as_dispatch": true,'
                                            ' "damage_observed": true, "confidence": 0.93, "notes": "torn strap visible"}')
    ev = tools.verify_evidence(oid, "live/demo.jpg", actor_sub=w["buyer"])
    assert ev["challenge_verified"] is True

    inj2 = tools.propose_refund(oid, 100_000, actor_sub=w["buyer"])
    assert inj2["decision"] == "deny" and inj2["reason"].startswith("amount_exceeds_held_funds")

    opts = tools.get_truce_options(oid, actor_sub=w["buyer"])
    assert [x["kind"] for x in opts["options"]] == ["partial_refund", "replacement", "store_credit"]
    partial = next(x for x in opts["options"] if x["kind"] == "partial_refund")
    assert partial["amount_minor"] == 2160  # 20% of 108.00

    buttons = tools.offer_options(oid, ["partial_refund", "replacement", "store_credit"], actor_sub=w["buyer"])
    assert len(buttons["buttons"]) == 3 and all(len(b["title"]) <= 20 for b in buttons["buttons"])

    res = tools.propose_refund(oid, partial["amount_minor"], actor_sub=w["buyer"])
    assert res["decision"] == "allow" and res["executed"] and res["status"] == "RESOLVED_PARTIAL_REFUND"
    final = w["stores"].get_order(oid)
    assert final["refunded_minor"] == 2160 and final["status"] == "RESOLVED_PARTIAL_REFUND"
    packs = [m for m in w["outbox"].for_channel("email") if "evidence pack" in (m.subject or "")]
    assert len(packs) == 2 and packs[0].attachments[0]["filename"].startswith("evidence-")
    decisions = w["stores"].decisions_for_order(oid)
    assert [d["decision"] for d in decisions] == ["allow", "deny", "allow", "deny", "deny", "allow"]


def test_escalation_approval_by_signed_link(world, monkeypatch):
    w = world
    o = buy_and_hold(w)
    dispatch(w, o["order_id"])
    oid = o["order_id"]
    tools.report_problem(oid, "Wrong item entirely.", actor_sub=w["buyer"])
    tools.verify_evidence(oid, "live/demo.jpg", actor_sub=w["buyer"])  # default stub verdict: verified

    full = tools.propose_refund(oid, 10800, actor_sub=w["buyer"])
    assert full["decision"] == "escalate" and full.get("escalated") is True
    o2 = w["stores"].get_order(oid)
    assert o2["status"] == "ESCALATED" and o2["tokens"]["approval"]
    mail = [m for m in w["outbox"].for_channel("email") if m.to == "supervisor@example.com"][-1]
    approve_url = re.search(r"Approve: (\S+)", mail.text).group(1)
    reject_url = re.search(r"Reject: (\S+)", mail.text).group(1)
    approve_token = parse_qs(urlparse(approve_url).query)["token"][0]
    reject_token = parse_qs(urlparse(reject_url).query)["token"][0]

    # tampering with the link (different amount in the claims) buys nothing
    from services.agentsafe import jws
    import json
    h, p, s = approve_token.split(".")
    claims = json.loads(jws.b64u_decode(p)); claims["amount_minor"] = 1
    forged = f"{h}.{jws.b64u(json.dumps(claims, separators=(',', ':'), sort_keys=True).encode())}.{s}"
    page = approvals.handle(forged)
    assert page["statusCode"] == 400 and w["stores"].get_order(oid)["status"] == "ESCALATED"
    assert [e["type"] for e in w["stores"].ledger_entries(oid)] == ["DISCOUNT", "HOLD"]

    page = approvals.handle(approve_token)
    assert page["statusCode"] == 200 and "approved and executed" in page["body"]
    final = w["stores"].get_order(oid)
    assert final["status"] == "RESOLVED_REFUND" and final["refunded_minor"] == 10800
    assert approvals.handle(approve_token)["statusCode"] == 200 and "no longer pending" in approvals.handle(approve_token)["body"]
    assert final["tokens"]["approval"] == ""
    assert [e["type"] for e in w["stores"].ledger_entries(oid)] == ["DISCOUNT", "HOLD", "REFUND"]
    # the reject link is dead now too
    assert "no longer pending" in approvals.handle(reject_token)["body"]


def test_escalation_rejected_returns_to_truce(world):
    w = world
    o = buy_and_hold(w)
    dispatch(w, o["order_id"])
    oid = o["order_id"]
    tools.report_problem(oid, "Scratched.", actor_sub=w["buyer"])
    tools.verify_evidence(oid, "live/demo.jpg", actor_sub=w["buyer"])
    tools.propose_refund(oid, 10800, actor_sub=w["buyer"])
    mail = [m for m in w["outbox"].for_channel("email") if m.to == "supervisor@example.com"][-1]
    reject_token = parse_qs(urlparse(re.search(r"Reject: (\S+)", mail.text).group(1)).query)["token"][0]
    page = approvals.handle(reject_token)
    assert page["statusCode"] == 200 and "rejected" in page["body"]
    back = w["stores"].get_order(oid)
    assert back["status"] == "DISPUTED" and back["tokens"]["dispute"] and back["dispute"]["reopened"]
    assert "did not approve" in w["outbox"].last("whatsapp").text
    # in-policy option still works after the rejection
    res = tools.propose_credit(oid, 3240, actor_sub=w["buyer"])  # 30% store credit
    assert res["decision"] == "allow" and res["status"] == "RESOLVED_CREDIT"


def test_webhook_signature_helpers():
    body = b'{"a":1}'
    sig = checkout.sign("s", body, ts=1000)
    assert checkout.verify("s", body, sig, now=1100)
    assert not checkout.verify("s", body, sig, now=5000)  # stale
    assert not checkout.verify("other", body, sig, now=1100)
    assert not checkout.verify("s", b'{"a":2}', sig, now=1100)
