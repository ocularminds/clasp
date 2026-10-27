"""Autonomous order: the Decionis AgentSafe gateway with its reference fallback, policy drift, stage-bound approvals,
the courier routes and settlement. The seller is only involved when policy escalates."""
from __future__ import annotations

import base64
import json

import pytest
import requests

from services.agentsafe import adapter, handler as agentsafe_handler
from services.agentsafe.policy import Policy, drift_signals, evaluate
from services.agentsafe.service import DecideRequest
from services.executor.executor import ExecRequest
from tests.conftest import BUYER


def refund(order, amount, evidence=None, **ctx):
    return DecideRequest("refund", order["order_id"], amount, "USD", BUYER,
                         evidence={"challenge_verified": True} if evidence is None else evidence, context=ctx,
                         correlation_id="corr-test")


VERDICT = {"code_visible": True, "code_matches": True, "same_item_as_dispatch": True, "damage_observed": True, "confidence": 0.93}


# --------------------------------------------------------------------------- drift
def test_drift_signals_each_escalate_an_in_policy_amount(policy, make_order):
    order = make_order(status="DISPUTED", amount=12000)
    ok = {"challenge_verified": True, "verdict": VERDICT}
    assert drift_signals(policy, "refund", 2400, order, ok, {}) == []  # 20% of 120.00 is on the ladder
    assert "off_ladder" in drift_signals(policy, "refund", 1000, order, ok, {})[0]
    assert "repeated_denials" in drift_signals(policy, "refund", 2400, order, ok, {"denials_on_order": 3})[0]
    assert "policy_version_change" in drift_signals(policy, "refund", 2400, order, ok, {"hold_policy_version": "2026.01.01-1"})[0]
    low = {**ok, "verdict": {**VERDICT, "confidence": 0.61}}
    assert "confidence 0.61" in drift_signals(policy, "refund", 2400, order, low, {})[0]
    other = {**ok, "verdict": {**VERDICT, "same_item_as_dispatch": False}}
    assert "does not match" in drift_signals(policy, "refund", 2400, order, other, {})[0]
    outcome, reason, _ = evaluate(policy, refund(order, 2400, low).to_dict(), order, 0, None, 0)
    assert outcome == "escalate" and reason.startswith("policy_drift")
    # a replacement is on the ladder, but inconclusive evidence still sends it to a human
    rep = DecideRequest("replacement", order["order_id"], 0, "USD", BUYER, evidence=low).to_dict()
    assert evaluate(policy, rep, order, 0, None, 0)[0] == "escalate"
    # hard limits stay denials, never escalations
    assert evaluate(policy, refund(order, 100_000, low).to_dict(), order, 0, None, 0)[0] == "deny"


def test_only_truce_denials_count_as_pressure(agentsafe, make_order):
    order = make_order(status="DISPUTED", amount=12000)
    for _ in range(3):
        agentsafe.decide(DecideRequest("discount", order["order_id"], 100, "USD", BUYER))  # wrong state: denied
    assert agentsafe.signals(order)["denials_on_order"] == 0
    for _ in range(3):
        agentsafe.decide(refund(order, 100_000))
    assert agentsafe.signals(order)["denials_on_order"] == 3
    d = agentsafe.decide(refund(order, 2400, {"challenge_verified": True, "verdict": VERDICT}))
    assert d.decision == "escalate" and "repeated_denials" in d.reason and "seller has been asked" in d.message


def test_settle_pays_the_seller_once_and_only_the_workflow_may_ask(agentsafe, executor, make_order, stores):
    order = make_order(status="RESOLVED_PARTIAL_REFUND", amount=10800, refunded_minor=2160)
    assert agentsafe.decide(DecideRequest("settle", order["order_id"], 8640, "USD", BUYER,
                                          evidence={"truce_resolved": True})).decision == "deny"  # not the buyer
    d = agentsafe.decide(DecideRequest("settle", order["order_id"], 8640, "USD", "system:workflow", evidence={"truce_resolved": True}))
    assert d.decision == "allow"
    r = executor.execute(ExecRequest("settle", order["order_id"], 8640, "USD", "system:workflow"), d.authorization)
    assert r.ok and stores.get_order(order["order_id"])["released_minor"] == 8640
    again = agentsafe.decide(DecideRequest("settle", order["order_id"], 8640, "USD", "system:workflow", evidence={"truce_resolved": True}))
    assert again.decision == "deny" and again.reason.startswith("amount_mismatch")  # nothing left to release


def test_a_seller_link_is_dead_at_agentsafe_once_the_supervisor_decides(agentsafe, make_order, stores):
    order = make_order(status="DISPUTED", amount=12000)
    esc = agentsafe.decide(refund(order, 12000, {"challenge_verified": True, "verdict": VERDICT}))
    assert esc.decision == "escalate"
    stores.update_order(order["order_id"], {"status": "ESCALATED", "escalation": {"stage": "supervisor"}})
    seller_token = agentsafe.approval_links(order["order_id"], esc.decision_id, 12000, "USD", "seller@example.com", "seller")["approve"]
    d = agentsafe.decide(refund(order, 12000, {"challenge_verified": True, "verdict": VERDICT}, approval_token=seller_token))
    assert d.decision == "deny" and d.reason.startswith("approval_required")
    sup = agentsafe.approval_links(order["order_id"], esc.decision_id, 12000, "USD", "sup@example.com", "supervisor")["approve"]
    d = agentsafe.decide(refund(order, 12000, {"challenge_verified": True, "verdict": VERDICT}, approval_token=sup))
    assert d.decision == "allow" and d.reason.startswith("supervisor_approved")


def test_policy_stages_and_document_agree():
    p = Policy.load()
    assert [s["role"] for s in p.stages] == ["seller", "supervisor"] and p.next_stage("seller")["role"] == "supervisor"
    assert p.next_stage("supervisor") is None


# --------------------------------------------------------------------------- Decionis gateway
class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


def gateway_client(agentsafe, monkeypatch, respond, mode="enforcement"):
    sent = []

    def post(url, data=None, headers=None, timeout=None):
        sent.append({"url": url, "body": json.loads(data), "headers": headers, "timeout": timeout})
        return respond(sent[-1])

    monkeypatch.setattr(requests, "post", post)
    return adapter.AgentSafeClient(url="https://clasp.decionisedge.com", service=agentsafe, tenant_key="tenant-key",
                                   upstream_token="upstream-token", timeout=3, mode=mode), sent


def test_gateway_relays_to_the_reference_service_and_keeps_its_signature(agentsafe, make_order, monkeypatch):
    order = make_order(status="DISPUTED", amount=12000)

    def upstream(req):  # what the gateway does: relay the same body to Clasp's /decide with the bearer token
        event = {"requestContext": {"http": {"method": "POST"}}, "rawPath": "/decide", "body": json.dumps(req["body"]),
                 "headers": {"authorization": req["headers"]["Authorization"], "x-agent-safe-dossier-id": "dsr_1",
                             "x-agent-safe-decision-id": "dcn_1"}}
        monkeypatch.setenv("AGENTSAFE_UPSTREAM_TOKEN", "upstream-token")
        out = agentsafe_handler.lambda_handler(event, None)
        return FakeResponse(out["statusCode"], json.loads(out["body"]),
                            {"agentsafe-execution": "FORWARDED", "agentsafe-decision": "ALLOW", "agentsafe-dossier-id": "dsr_1"})

    client, sent = gateway_client(agentsafe, monkeypatch, upstream)
    d = client.decide(action="refund", order_id=order["order_id"], amount_minor=2400, currency="USD", actor_sub=BUYER,
                      evidence={"challenge_verified": True, "verdict": VERDICT}, correlation_id="corr-test")
    assert d.decision == "allow" and d.authorization and d.engine == "decionis-gateway:enforcement"
    h = sent[0]["headers"]
    assert sent[0]["url"] == "https://clasp.decionisedge.com/decide" and h["AgentSafe-Tenant-Key"] == "tenant-key"
    assert h["Idempotency-Key"] and "tenant-key" not in sent[0]["url"]
    record = agentsafe.stores.decisions_for_order(order["order_id"])[-1]
    assert record["engine"] == "decionis-gateway" and record["context"]["decionis"] == {"dossier_id": "dsr_1", "decision_id": "dcn_1"}


FAILURES = [FakeResponse(401, {"code": "TENANT_KEY_INVALID"}, {"agentsafe-execution": "NOT_FORWARDED"}),
            FakeResponse(503, {"code": "UPSTREAM_UNVERIFIED"}, {"agentsafe-execution": "NOT_FORWARDED"}),
            FakeResponse(429, {"code": "RATE_LIMITED"}), requests.Timeout("slow")]


def _respond(failure):
    def respond(_):
        if isinstance(failure, Exception):
            raise failure
        return failure
    return respond


@pytest.mark.parametrize("failure", FAILURES)
def test_an_enforcing_gateway_that_cannot_answer_moves_nothing(agentsafe, make_order, monkeypatch, failure):
    order = make_order(status="DISPUTED", amount=12000)
    client, sent = gateway_client(agentsafe, monkeypatch, _respond(failure))
    d = client.decide(action="refund", order_id=order["order_id"], amount_minor=2400, currency="USD", actor_sub=BUYER,
                      evidence={"challenge_verified": True, "verdict": VERDICT})
    assert d.decision == "deny" and d.reason.startswith("decionis_unavailable") and not d.authorization
    assert "can't be reached" in d.message and len(sent) == 1  # no way around the gateway
    record = agentsafe.stores.decisions_for_order(order["order_id"])[-1]
    assert record["decision"] == "deny" and record["engine"] == "decionis-gateway"
    assert agentsafe.signals(order)["denials_on_order"] == 0  # an outage is not pressure from the buyer


@pytest.mark.parametrize("failure", FAILURES)
def test_a_shadow_gateway_that_cannot_answer_falls_back(agentsafe, make_order, monkeypatch, failure):
    order = make_order(status="DISPUTED", amount=12000)
    client, _ = gateway_client(agentsafe, monkeypatch, _respond(failure), mode="shadow")
    d = client.decide(action="refund", order_id=order["order_id"], amount_minor=2400, currency="USD", actor_sub=BUYER,
                      evidence={"challenge_verified": True, "verdict": VERDICT})
    assert d.decision == "allow" and d.engine == "reference"
    assert agentsafe.stores.decisions_for_order(order["order_id"])[-1]["context"]["gateway_fallback"]


def test_a_held_call_becomes_a_clasp_escalation(agentsafe, make_order, monkeypatch):
    order = make_order(status="DISPUTED", amount=12000)
    held = FakeResponse(202, {"state": "ESCALATE", "reason_codes": ["HUMAN_REVIEW"], "resume_token": "rt"})
    client, _ = gateway_client(agentsafe, monkeypatch, lambda _: held)
    d = client.decide(action="refund", order_id=order["order_id"], amount_minor=2400, currency="USD", actor_sub=BUYER,
                      evidence={"challenge_verified": True, "verdict": VERDICT})
    assert d.decision == "escalate" and d.reason == "decionis_escalate: HUMAN_REVIEW"
    assert d.message == "This needs a human check, so the seller has been asked to approve it."


def test_a_gateway_block_is_final_and_recorded(agentsafe, make_order, monkeypatch):
    order = make_order(status="DISPUTED", amount=12000)
    block = FakeResponse(403, {"state": "BLOCK", "reason_codes": ["POLICY_DENY"], "dossier_id": "dsr_9"})
    client, sent = gateway_client(agentsafe, monkeypatch, lambda _: block)
    d = client.decide(action="refund", order_id=order["order_id"], amount_minor=2400, currency="USD", actor_sub=BUYER,
                      evidence={"challenge_verified": True, "verdict": VERDICT})
    assert d.decision == "deny" and d.reason == "decionis_block: POLICY_DENY" and not d.authorization
    assert len(sent) == 1  # no retry against the fallback
    record = agentsafe.stores.decisions_for_order(order["order_id"])[-1]
    assert record["decision"] == "deny" and record["context"]["decionis"]["dossier_id"] == "dsr_9"


def test_upstream_proof_matches_the_documented_vector_and_is_served(monkeypatch):
    from services.agentsafe.upstream_proof import origin_of, token

    origin = origin_of("https://API.acme.example:443/some/base")
    assert origin == "https://api.acme.example"
    assert token("acme", "6f1c1e0e-2a8b-4a35-9c55-0d6f0a3d2b11", origin, 1759492800) == "v1.1759492800.GCO1YWc5UIsWe8_w9vbzTQ"
    ev = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/.well-known/agentsafe-upstream", "headers": {}}
    assert agentsafe_handler.lambda_handler(ev, None)["statusCode"] == 404
    monkeypatch.setenv("AGENTSAFE_UPSTREAM_PROOF", "v1.1.aaa\\nv1.2.bbb")  # a rotation's two tokens
    out = agentsafe_handler.lambda_handler(ev, None)
    assert out["statusCode"] == 200 and out["body"] == "v1.1.aaa\nv1.2.bbb\n" and out["headers"]["content-type"].startswith("text/plain")


@pytest.mark.parametrize("header,value", [("authorization", "Bearer right"), ("x-api-key", "right")])
def test_decide_accepts_the_credential_an_enforcing_gateway_injects(agentsafe, make_order, monkeypatch, header, value):
    order = make_order(status="DISPUTED", amount=12000)
    monkeypatch.setenv("AGENTSAFE_UPSTREAM_TOKEN", "right")
    monkeypatch.setenv("AGENTSAFE_UPSTREAM_HEADER", header)
    ev = {"requestContext": {"http": {"method": "POST"}}, "rawPath": "/decide", "headers": {header: value},
          "body": json.dumps(refund(order, 2400, {"challenge_verified": True, "verdict": VERDICT}).to_dict())}
    out = agentsafe_handler.lambda_handler(ev, None)
    assert out["statusCode"] == 200 and json.loads(out["body"])["decision"] == "allow"


def test_decide_over_http_needs_the_upstream_token(agentsafe, make_order, monkeypatch):
    order = make_order(status="DISPUTED", amount=12000)
    monkeypatch.setenv("AGENTSAFE_UPSTREAM_TOKEN", "right")
    body = json.dumps(refund(order, 2400).to_dict())
    base = {"requestContext": {"http": {"method": "POST"}}, "rawPath": "/decide", "body": body}
    for headers in ({}, {"authorization": "Bearer wrong"}):
        out = agentsafe_handler.lambda_handler({**base, "headers": headers}, None)
        assert out["statusCode"] == 401 and json.loads(out["body"])["decision"] == "deny"
    assert agentsafe.stores.decisions_for_order(order["order_id"]) == []
    monkeypatch.delenv("AGENTSAFE_UPSTREAM_TOKEN")
    assert agentsafe_handler.lambda_handler({**base, "headers": {"authorization": "Bearer "}}, None)["statusCode"] == 401  # fail closed


def test_an_enforcing_gateway_cannot_be_bypassed_by_invoking_the_lambda(agentsafe, make_order, monkeypatch):
    order = make_order(status="DISPUTED", amount=12000)
    req = refund(order, 2400, {"challenge_verified": True, "verdict": VERDICT}).to_dict()
    monkeypatch.setenv("AGENTSAFE_URL", "https://clasp.decionisedge.com")
    out = agentsafe_handler.lambda_handler(dict(req), None)
    assert out["decision"] == "deny" and out["reason"].startswith("gateway_required") and "authorization" not in out
    assert agentsafe.stores.decisions_for_order(order["order_id"]) == []  # nothing decided, nothing recorded
    # what the adapter still needs directly: record a gateway refusal (never an allow)
    rec = agentsafe_handler.lambda_handler({"op": "record_external", "request": req, "decision": "deny",
                                            "reason": "decionis_block: TEST", "engine": "decionis-gateway"}, None)
    assert rec["decision"] == "deny"
    # shadow keeps the direct fallback, and so does a deployment with no gateway at all
    monkeypatch.setenv("AGENTSAFE_GATEWAY_MODE", "shadow")
    assert agentsafe_handler.lambda_handler(dict(req), None)["decision"] == "allow"


# --------------------------------------------------------------------------- HTTP surfaces
def _http(method, path, body="", query=None):
    return {"requestContext": {"http": {"method": method}}, "rawPath": path, "body": body, "isBase64Encoded": False,
            "queryStringParameters": query or {}, "pathParameters": {}}


def test_courier_page_scans_a_pickup_photo(stores, make_order, monkeypatch, tmp_path, workflow):
    from services.common.media import LocalMedia, set_media
    from services.courier_mock import handler as courier_handler

    set_media(LocalMedia(tmp_path))
    try:
        order = make_order(status="HELD", amount=10800, shipment={"shipment_id": "shp_1", "status": "booked", "replacement": False})
        path = f"/courier/{order['order_id']}/shp_1"
        page = courier_handler.lambda_handler(_http("GET", path), None)
        assert page["statusCode"] == 200 and "capture='environment'" in page["body"] and order["order_id"][-6:] in page["body"]
        assert courier_handler.lambda_handler(_http("GET", f"/courier/{order['order_id']}/shp_x"), None)["body"].count("invalid") == 1
        bad = courier_handler.lambda_handler(_http("POST", path, json.dumps({"photo_b64": base64.b64encode(b"GIF89a").decode()})), None)
        assert bad["statusCode"] == 400
        photo = base64.b64encode(b"\xff\xd8\xff parcel").decode()
        ok = courier_handler.lambda_handler(_http("POST", path, json.dumps({"photo_b64": photo})), None)
        assert ok["statusCode"] == 200 and "Pickup scanned" in ok["body"]
        token, output = workflow.successes[-1]
        assert token == "tok-disp" and output["dispatch_photo_key"] == f"courier/{order['order_id']}/shp_1.jpg"
    finally:
        set_media(None)


def test_approval_get_never_acts(agentsafe, make_order, stores):
    from services.approvals import handler as approvals

    order = make_order(status="ESCALATED", amount=12000, escalation={"stage": "seller", "kind": "refund", "action": "refund",
                                                                     "amount_minor": 12000, "currency": "USD", "reason": "above_auto_cap"})
    links = agentsafe.approval_links(order["order_id"], "dec_x", 12000, "USD", "seller@example.com", "seller")
    page = approvals.lambda_handler(_http("GET", "/approvals", query={"token": links["reject"]}), None)
    assert page["statusCode"] == 200 and "Reject refund of $120.00?" in page["body"] and page["headers"]["cache-control"] == "no-store"
    assert stores.get_order(order["order_id"])["status"] == "ESCALATED"


def test_unanswered_gateway_retries_the_hold_and_the_settlement(stores, make_order, monkeypatch):
    from services.psp_mock import checkout
    from services.psp_mock.webhook import handle_webhook
    from services.workflows import tasks

    unavailable = {"decision": "deny", "reason": "decionis_unavailable: gateway HTTP 503", "message": "", "executed": False}
    monkeypatch.setattr("services.psp_mock.webhook.propose", lambda *a, **k: dict(unavailable))
    monkeypatch.setenv("PSP_WEBHOOK_SECRET", "s")
    order = make_order(status="PAYMENT_PENDING", amount=10800, checkout_id="chk_1")
    body = json.dumps(checkout.payment_event(order)).encode()
    status, out = handle_webhook(body, {checkout.SIG_HEADER: checkout.sign("s", body)})
    assert status == 503 and out["retry"] is True  # the provider retries; nothing was held

    monkeypatch.setattr("services.tools.money.propose", lambda *a, **k: dict(unavailable))
    settled = make_order(status="RESOLVED_PARTIAL_REFUND", amount=10800, refunded_minor=2160)
    with pytest.raises(tasks.DecionisUnavailable):
        tasks.settle_remainder(settled["order_id"])  # Step Functions retries SettleRemainder
