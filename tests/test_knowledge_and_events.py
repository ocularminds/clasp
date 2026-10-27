"""Policy knowledge (Bedrock Knowledge Base + local fallback) and order lifecycle events (EventBridge)."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import boto3
import pytest

from services.agentsafe import policy_doc
from services.common import events
from services.tools import impl as tools
from services.tools import knowledge
from tests.conftest import REGION
from tests.test_order_flow_e2e import buy_and_hold, dispatch, world  # noqa: F401  (fixture re-exported)


# ------------------------------------------------------------------ policy document
def test_rendered_policy_doc_is_current_and_carries_the_numbers():
    rendered = policy_doc.render()
    assert policy_doc.POLICY_DOC.read_text() == rendered, "run `make kb-sync ARGS=--render-only` after editing policy.yaml"
    assert "$50.00" in rendered and "30% of the order" in rendered and "at most 1 refund in any 30-day period" in rendered
    assert "up to 10% of the list price" in rendered and "20% partial refund" in rendered and "48 hours" in rendered
    titles = [s["title"] for s in policy_doc.sections(rendered)]
    assert titles[:3] == ["Overview", "Who can act on an order", "Discounts"] and "Refund limits" in titles


# ------------------------------------------------------------------ explain_policy tool
def test_explain_policy_falls_back_to_local_sections(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", "")
    r = tools.call("explain_policy", {"question": "How much can be refunded automatically?", "actor_sub": "x", "correlation_id": "c"})
    assert r["source"] == "local_policy" and r["policy_version"] == policy_doc.load_policy()["version"]
    assert "$50.00" in r["passages"][0]["text"] and r["passages"][0]["source"] == "compensation-policy.md"
    r2 = knowledge.explain_policy("why was my refund denied")
    assert any("Why was my refund denied" in p["text"] for p in r2["passages"])
    assert knowledge.explain_policy("zzzz qqqq")["passages"][0]["text"].startswith("Overview")
    assert tools.call("explain_policy", {"question": "", "actor_sub": "x"})["error"] == "question is required"


def test_explain_policy_uses_the_knowledge_base_and_falls_back_on_failure(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", "kb-123")
    calls = []

    class FakeAgentRuntime:
        def __init__(self, fail=False):
            self.fail = fail

        def retrieve(self, **kw):
            calls.append(kw)
            if self.fail:
                raise RuntimeError("AccessDeniedException")
            return {"retrievalResults": [{"content": {"text": "A refund is allowed automatically ... at most $50.00"}, "score": 0.81,
                                          "location": {"type": "S3", "s3Location": {"uri": "s3://docs/policy/compensation-policy.md"}}}]}

    monkeypatch.setattr(knowledge, "client", lambda name: FakeAgentRuntime())
    r = knowledge.explain_policy("refund limit?", correlation_id="c1")
    assert r["source"] == "knowledge_base" and r["knowledge_base_id"] == "kb-123"
    assert r["passages"][0]["score"] == 0.81 and r["passages"][0]["source"].endswith("compensation-policy.md")
    assert calls[0]["knowledgeBaseId"] == "kb-123" and calls[0]["retrievalQuery"] == {"text": "refund limit?"}
    assert calls[0]["retrievalConfiguration"]["vectorSearchConfiguration"]["numberOfResults"] == 4

    monkeypatch.setattr(knowledge, "client", lambda name: FakeAgentRuntime(fail=True))
    r = knowledge.explain_policy("refund limit?")
    assert r["source"] == "local_policy"  # retrieval failure never breaks the tool
    assert any("$50.00" in p["text"] or "automatic limit" in p["text"] for p in r["passages"])


def test_explain_policy_is_a_registered_read_only_tool():
    from services.tools.schema import TOOL_SCHEMAS

    assert "explain_policy" in tools.TOOLS
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "explain_policy")
    assert schema["inputSchema"]["required"] == ["question"]
    assert "Knowledge Base" in schema["description"]


# ------------------------------------------------------------------ events
def test_lifecycle_events_follow_the_order_through_escalation(world):
    from services.approvals import handler as approvals

    events.reset_recorder()
    w = world
    o = buy_and_hold(w)
    dispatch(w, o["order_id"])
    oid = o["order_id"]
    tools.report_problem(oid, "Wrong item entirely.", actor_sub=w["buyer"])
    tools.verify_evidence(oid, actor_sub=w["buyer"], media_key="live/demo.jpg")
    assert tools.propose_refund(oid, 10800, actor_sub=w["buyer"])["decision"] == "escalate"
    mail = [m for m in w["outbox"].for_channel("email") if m.to == "seller@example.com"][-1]
    token = parse_qs(urlparse(re.search(r"Approve: (\S+)", mail.text).group(1)).query)["token"][0]
    assert approvals.handle(token)["statusCode"] == 200

    rec = events.recorder()
    assert rec.types() == ["OrderCreated", "OrderHeld", "CourierBooked", "OrderDispatched", "DeliveryCodeSent", "TruceOpened",
                           "OrderEscalated", "ApprovalRequested", "OrderResolved"]
    for e in rec.events:
        assert e["order_id"] == oid and e["correlation_id"] == "corr-e2e" and e["stage"]
        blob = json.dumps(e).lower()
        assert "token" not in blob and "+1555" not in blob  # no secrets, no phone numbers
    by_type = {e["detail_type"]: e for e in rec.events}
    assert by_type["OrderHeld"]["status"] == "HELD" and by_type["OrderHeld"]["held_minor"] == 10800
    code_event = by_type["DeliveryCodeSent"]
    assert code_event["challenge_id"].startswith("chl_") and code_event["channel"] == "sms"
    assert not re.search(r"\b[A-Z0-9]{6}\b", code_event.get("notes", "") or "")
    assert by_type["OrderEscalated"]["requested_minor"] == 10800 and by_type["ApprovalRequested"]["stage"] == "seller"
    assert by_type["OrderResolved"]["outcome"] == "RESOLVED_REFUND" and by_type["OrderResolved"]["refunded_minor"] == 10800


def test_events_never_carry_codes_tokens_or_phones():
    with pytest.raises(ValueError, match="must not carry"):
        events.emit("OrderHeld", {"order_id": "ord_x"}, code="ABC123")
    with pytest.raises(ValueError, match="unknown event type"):
        events.emit("SomethingElse", {"order_id": "ord_x"})


def test_events_publish_to_the_eventbridge_bus(aws, monkeypatch):
    eb = boto3.client("events", region_name=REGION)
    eb.create_event_bus(Name="clasp-test")
    monkeypatch.setenv("EVENTS_MODE", "eventbridge")
    monkeypatch.setenv("EVENT_BUS_NAME", "clasp-test")
    from services.common.aws import reset_cache

    reset_cache()
    event_id = events.emit("OrderHeld", {"order_id": "ord_abc123", "status": "HELD", "amount_minor": 100, "currency": "USD",
                                         "correlation_id": "c"}, seller_notified=True)
    assert event_id  # moto returns an EventId
    monkeypatch.setenv("EVENT_BUS_NAME", "does-not-exist")
    assert events.emit("OrderHeld", {"order_id": "ord_abc123", "status": "HELD"}) is None  # failure is swallowed, not raised
    reset_cache()


def test_events_off_mode_is_silent(monkeypatch):
    monkeypatch.setenv("EVENTS_MODE", "off")
    assert events.emit("OrderCreated", {"order_id": "ord_1"}) is None
