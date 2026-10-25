"""M5 wiring: the Guardrail reaches the model config when configured, the evidence verdict is strict JSON
with the agreed keys, and a failed verdict yields no refund authorization."""
from __future__ import annotations

import json

import pytest

from services.tools import evidence


def test_guardrail_flows_into_bedrock_model_config(monkeypatch):
    pytest.importorskip("strands")
    from services.agent.agent import build_model

    monkeypatch.setenv("GUARDRAIL_ID", "gr-123")
    monkeypatch.setenv("GUARDRAIL_VERSION", "2")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    cfg = build_model().get_config()
    assert cfg["guardrail_id"] == "gr-123" and cfg["guardrail_version"] == "2"
    assert cfg["guardrail_redact_input"] is True and cfg["guardrail_redact_output"] is True
    assert cfg["model_id"].startswith("us.anthropic.claude")
    monkeypatch.delenv("GUARDRAIL_ID")
    assert not build_model().get_config().get("guardrail_id")


def test_evidence_verdict_is_strict_json_with_agreed_keys(monkeypatch):
    monkeypatch.setenv("EVIDENCE_MODE", "stub")
    monkeypatch.setenv("EVIDENCE_STUB_JSON", json.dumps({"code_visible": "true", "code_matches": 1, "same_item_as_dispatch": False,
                                                         "damage_observed": "yes", "confidence": "1.7", "notes": "x" * 900, "extra": 1}))
    v = evidence.analyze("ABC123", None, b"\xff\xd8\xff")
    assert set(v) == set(evidence.FIELDS)
    assert v["code_visible"] is True and v["code_matches"] is True and v["same_item_as_dispatch"] is False
    assert v["damage_observed"] is False  # "yes" is not an unambiguous boolean
    assert v["confidence"] == 1.0 and len(v["notes"]) == 500


def test_failed_verdict_blocks_refund(stores, kms, kms_key, agentsafe, tmp_path, monkeypatch):
    from demo.seed import seed
    from services.agentsafe.signer import KmsVerifier
    from services.common.media import LocalMedia, set_media
    from services.executor.executor import Executor, set_executor
    from services.notify.router import MemoryNotifier, set_notifier
    from services.psp_mock.handler import pay
    from services.tools import impl as tools
    from services.workflows.client import set_driver
    from services.workflows.local_runner import LocalDriver

    monkeypatch.setenv("PSP_WEBHOOK_SECRET", "s")
    driver = LocalDriver()
    set_driver(driver)
    set_executor(Executor(KmsVerifier(kms_key, kms), stores, driver))
    set_notifier(MemoryNotifier())
    media = LocalMedia(tmp_path)
    set_media(media)
    media.put("live/fake.jpg", b"\xff\xd8\xff")
    subs = seed(stores)
    buyer, seller = subs["buyer_demo"], subs["seller_demo"]
    try:
        o = tools.start_order("item_bag", actor_sub=buyer)
        pay(tools.create_checkout(o["order_id"], actor_sub=buyer)["checkout_url"].rsplit("/", 1)[-1])
        tools.record_dispatch(o["order_id"], actor_sub=seller, photo_key="d.jpg")
        tools.report_problem(o["order_id"], "torn", actor_sub=buyer)
        monkeypatch.setenv("EVIDENCE_STUB_JSON", json.dumps({"code_visible": True, "code_matches": False, "same_item_as_dispatch": True,
                                                             "damage_observed": True, "confidence": 0.95, "notes": "code reads 999999"}))
        v = tools.verify_evidence(o["order_id"], "live/fake.jpg", actor_sub=buyer)
        assert v["challenge_verified"] is False
        r = tools.propose_refund(o["order_id"], 2000, actor_sub=buyer)
        assert r["decision"] == "deny" and r["reason"].startswith("evidence_required")
        assert [e["type"] for e in stores.ledger_entries(o["order_id"])] == ["HOLD"]
    finally:
        set_driver(None); set_executor(None); set_notifier(None); set_media(None)
